import asyncio
from gen1_data import load_gen1_ou_data
from team_generator import generate_team
from battle_runner import run_all_battles

async def main():
    ou_data = load_gen1_ou_data()
    
    feedback = None
    
    for iteration in range(5):  # 5 improvement cycles
        print(f"\n{'='*50}")
        print(f"ITERATION {iteration + 1}")
        print(f"{'='*50}")
        
        # Generate team
        team = generate_team(ou_data, battle_feedback=feedback)
        if not team:
            print("Failed to generate valid team, stopping")
            break
            
        # Save current team
        with open(f"team_iteration_{iteration+1}.txt", "w") as f:
            f.write(team)
        
        # Run battles and collect feedback
        feedback = await run_all_battles(team, n_battles=10)
        
        # Save feedback
        with open(f"feedback_iteration_{iteration+1}.txt", "w") as f:
            f.write(feedback)
        
        print(f"\n📋 Feedback for next iteration:")
        print(feedback)

asyncio.run(main())