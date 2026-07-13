import json
from urllib.parse import urlencode
from urllib.request import urlopen


class OnlineAI:
    CONNECT4_URL = "https://kevinalbs.com/connect4/back-end/index.php"

    def __init__(self):
        self.get_moves_endpoint = f"{self.CONNECT4_URL}/getMoves"
        self.has_won_endpoint = f"{self.CONNECT4_URL}/hasWon"

    def get_best_online_move(self, board_data, player=2):
        params = urlencode({"board_data": board_data, "player": player})
        try:
            with urlopen(f"{self.get_moves_endpoint}?{params}", timeout=10) as response:
                moves = json.loads(response.read().decode("utf-8"))
        except Exception as error:
            print(f"Error communicating with the Connect 4 API: {error}")
            return None

        best_move = max(moves, key=moves.get)
        return int(best_move)

    def has_won(self, board_data, player, i, j):
        params = urlencode({
            "board_data": board_data,
            "player": player,
            "i": i,
            "j": j,
        })
        try:
            with urlopen(f"{self.has_won_endpoint}?{params}", timeout=10) as response:
                return json.loads(response.read().decode("utf-8"))
        except Exception as error:
            print(f"Error communicating with the Connect 4 API: {error}")
            return False
