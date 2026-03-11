import asyncio
from gen1_data import load_format_data
from team_generator import generate_team
from battle_runner import run_all_battles

FORMAT = "OU"  # Change this to run a different format

async def main():
    print(f"Loading Gen 1 {FORMAT} data...")
    format_data = load_format_data(FORMAT)
    print(f"Pool: {len(format_data)} Pokemon\n")

    feedback = None

    for iteration in range(5):  # 5 improvement cycles
        print(f"\n{'='*50}")
        print(f"ITERATION {iteration + 1}")
        print(f"{'='*50}")

        # Generate team
        team = generate_team(format_data, format_name=FORMAT, battle_feedback=feedback)
        if not team:
            print("Failed to generate valid team, stopping")
            break

        # Save current team
        filename = f"team_{FORMAT.lower()}_iteration_{iteration+1}.txt"
        with open(filename, "w") as f:
            f.write(team)
        print(f"Saved team to {filename}")

        # Run battles and collect feedback
        feedback = await run_all_battles(team, n_battles=10)

        # Save feedback
        filename = f"feedback_{FORMAT.lower()}_iteration_{iteration+1}.txt"
        with open(filename, "w") as f:
            f.write(feedback)

        print(f"\n📋 Feedback for next iteration:")
        print(feedback)

asyncio.run(main())