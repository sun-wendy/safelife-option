import numpy as np


class TermConditions:
    def __init__(self):
        pass
    
    def can_terminate(self, cur_state):
        # cur_state: (26,, 26, 15) np array
        term_conditions = [self.reach_exit, self.destroy_red_cells]
        return any([term_condition(cur_state) for term_condition in term_conditions])
    
    def reach_exit(self, cur_state):
        agent_pos = cur_state[:,:,0]
        exit_pos = cur_state[:,:,6]
        return np.all(agent_pos == exit_pos)
    
    def destroy_red_cells(self, cur_state):
        red_cells = cur_state[:,:,7]
        subgrids = [red_cells[i:i+7, j:j+7] for i in range(0, 26, 7) for j in range(0, 26, 7)]
        return any([np.all(subgrid == 0) for subgrid in subgrids])
    
    def create_gray_cells(self, cur_state):
        gray_cells = cur_state[:,:,13]
        blue_region = cur_state[:,:,10]
        gray_cells_subgrids = [gray_cells[i:i+7, j:j+7] for i in range(0, 26, 7) for j in range(0, 26, 7)]
        blue_region_subgrids = [blue_region[i:i+7, j:j+7] for i in range(0, 26, 7) for j in range(0, 26, 7)]
        return any([np.all(gray_cells_subgrid == blue_region_subgrid) for gray_cells_subgrid, blue_region_subgrid in zip(gray_cells_subgrids, blue_region_subgrids)])
    