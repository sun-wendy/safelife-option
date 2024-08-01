class TermConditions:
    def __init__(self, term_set):
        # term_set: dictionary of termination conditions
        # key: index of state factors
        # values: list of acceptable values for the state factor
        # If a state factor is not in term_set, it can take any value
        self.term_set = term_set
    
    def can_terminate(self, cur_state):
        # cur_state: current state in full factored form
        for i, state_factor in enumerate(cur_state):
            if i in self.term_set:
                if state_factor not in self.term_set[i]:
                    return False
        return True
