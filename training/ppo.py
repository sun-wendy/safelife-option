import logging
import numpy as np
from collections import defaultdict

import torch
import torch.optim as optim

from safelife.helper_utils import load_kwargs
from safelife.random import get_rng

from .base_algo import BaseAlgo
from .global_config import HyperParam, update_hyperparams
from .utils import named_output, round_up


logger = logging.getLogger(__name__)
USE_CUDA = torch.cuda.is_available()


@update_hyperparams
class PPO(BaseAlgo):
    data_logger = None  # SafeLifeLogger instance

    num_steps = 0

    steps_per_env: HyperParam = 20
    num_minibatches: HyperParam = 4
    epochs_per_batch: HyperParam = 3

    gamma: HyperParam = 0.97
    lmda: HyperParam = 0.95
    learning_rate: HyperParam = 3e-4
    entropy_reg: HyperParam = 0.01
    # don't start regularization until entropy < entropy_clip
    entropy_clip: HyperParam = 1.0
    vf_coef: HyperParam = 0.5
    eps_policy: HyperParam = 0.2  # PPO clipping for policy loss
    eps_value: HyperParam = 0.2  # PPO clipping for value loss

    report_interval = 960
    test_interval = 500000

    compute_device = torch.device('cuda' if USE_CUDA else 'cpu')

    training_envs = None
    testing_envs = None

    checkpoint_attribs = ('model', 'optimizer', 'data_logger.cumulative_stats')

    def __init__(self, model, **kwargs):
        load_kwargs(self, kwargs)
        assert self.training_envs is not None

        self.model = model.to(self.compute_device)
        self.optimizer = optim.Adam(
            self.model.parameters(), lr=self.learning_rate)

        self.load_checkpoint()

    @named_output('obs actions rewards done next_obs agent_ids policies values')
    def take_one_step(self, envs):
        obs, agent_ids = self.obs_for_envs(envs)

        tensor_obs = self.tensor(obs, torch.float32)
        values, policies = self.model(tensor_obs)
        values = values.detach().cpu().numpy()
        policies = policies.detach().cpu().numpy()
        actions = [get_rng().choice(len(policy), p=policy) for policy in policies]

        next_obs, rewards, done = self.act_on_envs(envs, actions)

        return obs, actions, rewards, done, next_obs, agent_ids, policies, values

    @named_output('obs actions action_prob returns advantages values')
    def gen_training_batch(self, steps_per_env):
        """
        Run each environment a number of steps and calculate advantages.

        Note that the output is flat, i.e., a single list of observations,
        actions, etc.

        Parameters
        ----------
        steps_per_env : int
            Number of steps to take per environment.
        """
        assert steps_per_env > 0

        trajectories = defaultdict(lambda: {
            'obs': [],
            'actions': [],
            'action_prob': [],
            'rewards': [],
            'values': [],
            'final_value': 0.0,
        })

        # Take a bunch of steps, and put them into trajectories associated with
        # each distinct agent
        for _ in range(steps_per_env):
            step = self.take_one_step(self.training_envs)
            for k, agent_id in enumerate(step.agent_ids):
                t = trajectories[agent_id]
                action = step.actions[k]
                t['obs'].append(step.obs[k])
                t['actions'].append(action)
                t['action_prob'].append(step.policies[k, action])
                t['rewards'].append(step.rewards[k])
                t['values'].append(step.values[k])

        # For the final step in each environment, also calculate the value
        # function associated with the next observation
        tensor_obs = self.tensor(step.next_obs, torch.float32)
        vals = self.model(tensor_obs)[0].detach().cpu().numpy()
        for k, agent_id in enumerate(step.agent_ids):
            if not step.done[k]:
                trajectories[agent_id]['final_value'] = vals[k]

        # Calculate the discounted rewards for each trajectory
        gamma = self.gamma
        lmda = self.lmda
        for t in trajectories.values():
            val0 = np.array(t['values'])
            val1 = np.append(t['values'][1:], t['final_value'])
            rewards = returns = np.array(t['rewards'])
            advantages = rewards + gamma * val1 - val0
            returns[-1] += gamma * t['final_value']
            for i in range(len(rewards) - 2, -1, -1):
                returns[i] += gamma * returns[i+1]
                advantages[i] += lmda * advantages[i+1]
            t['returns'] = returns
            t['advantages'] = advantages

        self.num_steps += steps_per_env * len(self.training_envs)

        def t(label, dtype=torch.float32):
            x = np.concatenate([d[label] for d in trajectories.values()])
            return torch.as_tensor(x, device=self.compute_device, dtype=dtype)

        return (
            t('obs'), t('actions', torch.int64), t('action_prob'),
            t('returns'), t('advantages'), t('values')
        )

    def calculate_loss(
            self, obs, actions, old_policy, old_values, returns, advantages):
        """
        All parameters ought to be tensors on the appropriate compute device.
        """
        values, policy = self.model(obs)
        a_policy = torch.gather(policy, -1, actions[..., np.newaxis])[..., 0]

        prob_diff = advantages.sign() * (1 - a_policy / old_policy)
        policy_loss = advantages.abs() * torch.clamp(prob_diff, min=-self.eps_policy)
        policy_loss = policy_loss.mean()

        v_clip = old_values + torch.clamp(
            values - old_values, min=-self.eps_value, max=+self.eps_value)
        value_loss = torch.max((v_clip - returns)**2, (values - returns)**2)
        value_loss = value_loss.mean()

        entropy = torch.sum(-policy * torch.log(policy + 1e-12), dim=-1)
        entropy_loss = torch.clamp(entropy.mean(), max=self.entropy_clip)
        entropy_loss *= -self.entropy_reg

        return entropy, policy_loss + value_loss * self.vf_coef + entropy_loss

    def train_batch(self, batch):
        num_samples = len(batch.obs)
        idx = np.arange(num_samples)
        splits = np.linspace(
            0, num_samples, self.num_minibatches+2, dtype=int)[1:-1]

        for _ in range(self.epochs_per_batch):
            get_rng().shuffle(idx)
            for k in np.split(idx, splits):
                entropy, loss = self.calculate_loss(
                    batch.obs[k], batch.actions[k], batch.action_prob[k],
                    batch.values[k], batch.returns[k], batch.advantages[k])
                self.optimizer.zero_grad()
                loss.backward()
                self.optimizer.step()

    def train(self, steps, wandb_run=None):
        max_steps = self.num_steps + steps

        while self.num_steps < max_steps:
            next_report = round_up(self.num_steps, self.report_interval)
            next_test = round_up(self.num_steps, self.test_interval)

            batch = self.gen_training_batch(self.steps_per_env)
            self.train_batch(batch)

            self.save_checkpoint_if_needed()

            num_steps = self.num_steps

            if num_steps >= next_report and self.data_logger is not None:
                entropy, loss = self.calculate_loss(
                    batch.obs, batch.actions, batch.action_prob,
                    batch.values, batch.returns, batch.advantages)
                loss = loss.item()
                entropy = entropy.mean().item()
                values = batch.values.mean().item()
                advantages = batch.advantages.mean().item()
                logger.info(
                    "n=%i: loss=%0.3g, entropy=%0.3f, val=%0.3g, adv=%0.3g",
                    num_steps, loss, entropy, values, advantages)
                self.data_logger.log_scalars({
                    "loss": loss,
                    "entropy": entropy,
                    "values": values,
                    "advantages": advantages,
                }, num_steps, 'ppo')

                if wandb_run is not None:
                    wandb_run.log({"loss": loss,
                                   "entropy": entropy,
                                   "values": values,
                                   "advantages": advantages}, num_steps)

            if self.testing_envs and num_steps >= next_test:
                self.run_episodes(self.testing_envs)

        self.save_checkpoint()
