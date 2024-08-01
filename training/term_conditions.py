import numpy as np


class TermConditions:
    def __init__(self):
        pass
    
    def can_terminate(self, cur_state):
        term_conditions = [self.reach_exit, self.check_red_cells]
        return any([term_condition(cur_state) for term_condition in term_conditions])
    
    def reach_exit(self, cur_state):
        agent_pos = cur_state[:,:,0]
        exit_pos = cur_state[:,:,6]
        return np.all(agent_pos == exit_pos)
    
    def check_red_cells(self, cur_state):
        red_cells = cur_state[:,:,7]
        return np.all(red_cells[:13,:13] == 0) or np.all(red_cells[:13,13:] == 0) or np.all(red_cells[13:,:13] == 0) or np.all(red_cells[13:,13:] == 0)
    