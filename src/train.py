"""
Train the DRL agent
"""

# Standard library imports
import csv

# 3rd party imports
import numpy as np
import torch
import os.path
from tqdm import tqdm

# Module imports
from collections import defaultdict
from config import get_config
from utils import plot_training_history
from actor_critic import Actor
from rl_env import StatesGenerator, get_benchmark_rewards,compute_reward, critical_task_reward
from inference import get_total_reward

def train(config):    
    
    torch.manual_seed(config.seed)
    np.random.seed(config.seed)
    states_generator = StatesGenerator(config)
    agent = Actor(config)
    
    alpha = config.alpha
    # Calculate average reward of benchmark heuristics
    print("Calculating average benchmark heuristics")
    nf_reward, ff_reward, ffd_reward = get_benchmark_rewards(config, states_generator)
    total_nf_reward = get_total_reward(alpha, nf_reward['avg_occ'], nf_reward['ci'])
    total_ff_reward = get_total_reward(alpha, ff_reward['avg_occ'], ff_reward['ci'])
    total_ffd_reward = get_total_reward(alpha, ffd_reward['avg_occ'], ffd_reward['ci'])

    print(nf_reward,ff_reward,ffd_reward)

    print(total_nf_reward,total_ff_reward,total_ffd_reward)

    # Training loop
    agent_rewards = defaultdict(list)
    pbar = tqdm(range(config.n_episodes))
    for i in pbar:
        states, states_lens, len_mask = states_generator.generate_states_batch()
        items_with_critical, critical_copy_mask, ci_groups = states_generator.generate_critical_items(
            states, len_mask, states_lens
        )
        
        agent_reward, predicted_reward,agent_ci_reward,agent_avg_occ = agent.reinforce_step(
            items_with_critical,
            states_lens,
            critical_copy_mask,
            ci_groups
        )
        agent_rewards['total_reward'].append(agent_reward)
        agent_rewards['critical_reward'].append(agent_ci_reward)
        agent_rewards['avg_occupancy'].append(agent_avg_occ)
        
        # Update progress bar
        pbar.set_description(
            f"Agent Total reward: {agent_reward:.1%} | "
            f"Critic pred. reward: {predicted_reward:.1%}"
            f"Avg occup. reward: {agent_avg_occ:.1%}"
        )
        
        if (i % 1000 == 0 and i > 0) or i == config.n_episodes - 1:
            # Decay learning rate
            agent.lr_scheduler_actor.step()
            agent.lr_scheduler_critic.step()
            # Plot training history
            plot_training_history(
                config,
                [
                    agent_rewards['total_reward'],
                    agent_rewards['critical_reward'],
                    agent_rewards['avg_occupancy'],
                    [total_nf_reward] * config.n_episodes,
                    [total_ff_reward] * config.n_episodes,
                    [total_ffd_reward] * config.n_episodes,
                ],
                ["DRL Agent total_reward ","DRL Agent Critical Reward", "DRL Agent Avg Occupancy Reward",  "NF", "FF", "FFD"],
                outfilepath="../experiments/policy_dnn_10_20_NF_3_Decoder.png",
                moving_avg_window=200,
            )


    # Save key training metrics
    if not os.path.isfile("../experiments/experiments.csv"):
        col_headers = list(vars(config).keys())
        col_headers.extend(["agent_reward", "nf_reward", "ff_reward", "ffd_reward"])
        with open("../experiments/experiments.csv", "w") as f:
            writer = csv.writer(f)
            writer.writerow(col_headers) 
    
    with open("../experiments/experiments.csv", "a") as f:
        writer = csv.writer(f)
        row_values = list(vars(config).values())
        row_values.extend([np.mean(agent_rewards['total_reward'][-100:]), total_nf_reward, total_ff_reward, total_ffd_reward])
        writer.writerow(row_values)

    # Save trained actor model
    if config.model_path:
        torch.save(agent.policy_dnn, config.model_path)
    
if __name__ == "__main__":

    # Train with time profiling
    import cProfile, pstats, io
    profiler = cProfile.Profile()
    profiler.enable()
    config, _ = get_config()
    train(config)
    profiler.disable()
    s = io.StringIO()
    stats = pstats.Stats(profiler, stream=s).strip_dirs().sort_stats("cumtime")
    stats.print_stats()
    with open("./profiler_output.txt", "w") as f:
            f.write(s.getvalue())
