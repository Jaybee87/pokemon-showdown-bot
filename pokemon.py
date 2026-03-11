import asyncio
from poke_env.player import RandomPlayer

async def main():
    # Two random bots battle each other
    player1 = RandomPlayer(
        battle_format="gen1ou",
        server_configuration=None,  # defaults to localhost:8000
        log_level=25
    )
    
    player2 = RandomPlayer(
        battle_format="gen1ou",
        server_configuration=None,
        log_level=25
    )

    await player1.battle_against(player2, n_battles=1)
    
    print(f"Player 1 won {player1.n_won_battles} / 1 battles")

asyncio.run(main())