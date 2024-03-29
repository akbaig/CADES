import gym
import random
import numpy as np
from gym import spaces
from enum import Enum
from states_generator import StatesGenerator
from config import get_config

class TerminationCause(Enum):
    SUCCESS = 1
    DUBLICATE_PICK = 2
    BIN_OVERFLOW = 3
    DUPLICATE_CRITICAL_PICK = 4


class CadesEnv(gym.Env):
    """Custom Environment that follows gym interface."""

    metadata = {"render.modes": ["human"]}

    def __init__(self, config):
        super().__init__()

        self.config = config
        self.states_generator = StatesGenerator(config)
        self.norm_factor = None

        self.action_space = spaces.MultiDiscrete(
            [config.max_num_items, config.total_bins]
        )
        
        self.observation_space = spaces.Dict(
            {
                "tasks": spaces.Box(
                    low=0, high=1, shape=(config.max_num_items,), dtype=np.float
                ),
                "critical_mask": spaces.MultiDiscrete(
                    [2 + config.number_of_critical_items] * np.prod((config.max_num_items,))
                ),
                "nodes": spaces.Box(
                    low=0, high=1, shape=(config.total_bins,), dtype=np.float
                ),
                "communications": spaces.Box(
                    low=0, high=1, shape=(config.max_num_items, config.max_num_items), dtype=np.uint8
                )
            }
        )

        self.env_stats = {}
        self.assignment_status = []
        for i in range(config.total_bins):
            self.assignment_status.append([])
        self.current_state = {}
        self.total_reward = 0
        self.done = False

    def _is_item_critical(self, item_index):
        # if item has mask value greater than one, it's defined as critical task
        return self.current_state["critical_mask"][item_index] > 1

    def _is_critical_item_duplicated(self, item_index, bin_index):
        critical_mask = self.current_state["critical_mask"]
        # get indices which have same mask value
        replica_indices = list(np.where(critical_mask == critical_mask[item_index])[0]) 
        # check if these indices are in the assignment status of selected bin
        return np.intersect1d(replica_indices, self.assignment_status[bin_index]).size > 0

    def get_item_receivers(self, item_index):
        receivers =  self.current_state["communications"][item_index]
        return np.where(receivers == 1)[0]
    
    def get_item_senders(self, item_index):
        senders =  self.current_state["communications"][:, item_index]
        return np.where(senders == 1)[0]
    
    def get_items_placed_in_bin(self, list_of_items, bin_index):
       return np.intersect1d(list_of_items, self.assignment_status[bin_index])

    # For Preventing the agent from choosing already chosen indices again
    # This method masks the invalid choices
    # And makes the agent choose a valid action instead 
    def get_valid_action(self, action):
        item_idx, bin_idx = action
        if self.current_state["tasks"][item_idx] > 0:
            return action  # The action is already valid
        valid_item_indices = [idx for idx, task in enumerate(self.current_state["tasks"]) if task > 0]
        if not valid_item_indices: # No valid item left
            raise ValueError('No valid action found')
        else: # Choose a new item from the valid items
            new_item_idx = random.choice(valid_item_indices)
        return [new_item_idx, bin_idx]
    
    def _reward(self,action):

        done = False
        # Agent outputs two actions, one for item index and one for bin index
        selected_item_idx = action[0]
        selected_bin_idx = action[1]
        selected_item_cost = self.current_state["tasks"][selected_item_idx]
        reward_type = ''
        # Agent picked the item which is already used
        if selected_item_cost == 0:
            # Agent receives reward based on its step number. Highest at early steps, lower at later steps.
            reward = self.config.DUPLICATE_PICK_reward - (abs(self.config.max_num_items - self.info['episode_len'])*3/self.config.max_num_items)
            # Select any other valid action
            new_action = self.get_valid_action(action)
            _, done = self._reward(new_action)
        # Agent picked the bin which is already full
        elif selected_item_cost > self.current_state["nodes"][selected_bin_idx]:
            reward = self.config.BIN_OVERFLOW_reward * self.info['episode_len'] * 0.25
            reward_type = 'Bin Overflow Reward'
            done = True
            self.info["termination_cause"] = TerminationCause.BIN_OVERFLOW.name
        # Agent picked the bin which already had critical task
        elif self._is_item_critical(selected_item_idx) and self._is_critical_item_duplicated(selected_item_idx, selected_bin_idx):
            reward = self.config.DUPLICATE_CRITICAL_PICK_reward * self.info['episode_len'] * 0.15
            reward_type = 'Duplicate Critical Pick Reward'
            done = True
            self.info["termination_cause"] = TerminationCause.DUPLICATE_CRITICAL_PICK.name
        # Agent picked the correct item and bin
        else:
            # Assign Rewards            
            reward = self.config.STEP_reward
            reward += self.config.BONUS_reward * self.info['episode_len']
            reward_type = 'Step and Bonus Reward'
            if self._is_item_critical(selected_item_idx):
                reward += self.config.CRITICAL_reward
                reward_type += ' \nCritical Reward'
            # Check if the item is communicating
            item_receivers = self.get_item_receivers(selected_item_idx)
            item_senders = self.get_item_senders(selected_item_idx)
            # if the item is a sender i.e has receivers
            if(len(item_receivers) > 0): 
                # narrow down the receivers to the ones that are already placed in the bin
                allocated_receivers = self.get_items_placed_in_bin(item_receivers, selected_bin_idx)
                # construct tuple pairs of such receivers and selected item
                allocated_pairs = list(map(lambda x: (selected_item_idx, x), allocated_receivers))
                # extract pairs which have yet not been allocated
                unallocated_pairs = list(set(allocated_pairs) - self.communication_status)
                if(len(unallocated_pairs) > 0):
                    # select a valid comm pair
                    pair = random.choice(unallocated_pairs)
                    # add the pair to the communication status for record keeping
                    self.communication_status.add(pair)
                    # assign normalized reward
                    reward += (self.config.COMM_reward/self.env_stats["comms_len"])
                    reward_type += f' \nCommunication Reward for {pair}'
            # if the item is a receiver, i.e. has senders
            if(len(item_senders) > 0):
                # narrow down the senders to the ones that are already placed in the bin
                allocated_senders = self.get_items_placed_in_bin(item_senders, selected_bin_idx)
                # construct tuple pairs of such senders and selected item
                allocated_pairs = list(map(lambda x: (x, selected_item_idx), allocated_senders))
                # extract pairs which have yet not been allocated
                unallocated_pairs = list(set(allocated_pairs) - self.communication_status)
                if(len(unallocated_pairs) > 0):
                    # select a valid comm pair
                    pair = random.choice(unallocated_pairs)
                    # add the pair to the communication status for record keeping
                    self.communication_status.add(pair)
                    # assign normalized reward
                    reward += (self.config.COMM_reward/self.env_stats["comms_len"])
                    reward_type += f' \nCommunication Reward for {pair}'
            # Mark the selected item as zero
            self.current_state["tasks"][selected_item_idx] = 0
            # Consume the space in selected bin
            self.current_state["nodes"][selected_bin_idx] -= selected_item_cost
            # Update Assignment status
            self.assignment_status[selected_bin_idx].append(selected_item_idx)
            self.info["episode_len"] = self.info["episode_len"] + 1
            # Check if no task is remaining
            if sum(self.current_state["tasks"]) == 0:
                reward += self.config.SUCCESS_reward
                reward_type += ' \n Success Reward'
                self.info["termination_cause"] = TerminationCause.SUCCESS.name
                self.info["is_success"] = True
                done = True

        return reward,done

    def step(self, action):
        observation = self.current_state
        reward,done = self._reward(action)
        if done is True:
            print("Observation Space: \nTasks: ", self.current_state["tasks"], " \nCritical Masks: ", self.current_state["critical_mask"], " \nNodes:", self.current_state["nodes"], " \nPossible Communications: ", self.env_stats["comms_len"])
            print("Last Action : Selected Item: ", action[0], " Selected Bin: ", action[1])
            print("Accounted Communications: ", len(self.communication_status))
            print("Episode Reward: ", reward, " Termination Cause: ", self.info["termination_cause"])

        self.info["assignment_status"] = self.assignment_status
        self.total_reward = self.total_reward + reward
        return observation, reward, done, self.info

    def reset(self):
        # assignment status is an variable-sized 2D Array, having dimensions total_bins x (size of bin)
        # it stores the indices of task assignment on the nodes
        self.assignment_status = []
        self.communication_status = set()
        for i in range(self.config.total_bins):
            self.assignment_status.append([])


        self.info = {"is_success": False, "episode_len": 0, "termination_cause": None}
        self.done = False
        self.total_reward = 0
        # states_batch_generator gives the following variables
        # states - 2D Array (Batch Size x num of items) - elements contain size of task
        # states_lens - 1D Array (Batch Size) - elements contain num of valid items in each row of states variable
        # states_mask - 2D Array (Batch Size x num of items) - elements represent a mask of states variable having all 1 values
        # bins_available - 2D Array (Batch Size x num of bins) - elements contain size of bins (imp: all batches contain same values of bin sizes)
        (
            states,
            states_lens,
            states_mask,
            bins_available,
        ) = self.states_generator.generate_states_batch()
        (
            states, critical_mask, _
        ) = self.states_generator.generate_critical_items(
            states,
            states_mask,
            states_lens
        )
        (
            communications,
            communications_lens
        ) = self.states_generator.generate_communications(
            states,
            critical_mask,
            states_lens
        )
        # norm factor is the largest bin size of first batch in bins_available
        self.norm_factor = max(list(bins_available[0]))

        # use first batch of states and bins_available and normalize the values, this is our observation now
        observation = {
            "tasks": np.array(list(states[0]) / self.norm_factor),
            "critical_mask": np.array(critical_mask[0]),
            "nodes": np.array(list(bins_available[0]) / self.norm_factor),
            "communications": np.array(communications[0])
        }
        self.current_state = observation
        self.env_stats["comms_len"] = communications_lens[0]
        self.env_stats["tasks_total_cost"] = sum(
            observation["tasks"] * self.norm_factor
        )
        self.env_stats["nodes_total_capacity"] = sum(
            observation["nodes"] * self.norm_factor
        )
        self.env_stats["extra_capacity"] = (
            round(
                1
                - (
                    self.env_stats["tasks_total_cost"]
                    / self.env_stats["nodes_total_capacity"]
                ),
                2,
            )
            * 100
        )

        return observation

    def render(self, mode="human"):
        pass

    def get_env_info(self):
        return self.env_stats

    def close(self):
        pass
