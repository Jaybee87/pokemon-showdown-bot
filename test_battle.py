import asyncio
from poke_env.player import RandomPlayer
from poke_env import LocalhostServerConfiguration

async def main():
    player1 = RandomPlayer(
        battle_format="gen1randombattle",
        server_configuration=LocalhostServerConfiguration,
        log_level=25
    )
    
    player2 = RandomPlayer(
        battle_format="gen1randombattle",
        server_configuration=LocalhostServerConfiguration,
        log_level=25
    )

    await player1.battle_against(player2, n_battles=1)
    
    print(f"Player 1 won {player1.n_won_battles} / 1 battles")

asyncio.run(main())