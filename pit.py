class Pit:
    def __init__(self, playercount, scoretowin):
        self.playercount = playercount
        self.scoretowin = scoretowin
        self.available_cards = {
            'Wheat': 100,
            'Coffee': 80,
            'Corn': 75,
            'Oranges': 50,
            'Barley': 85,
            'Oats': 60,
            'Soybeans': 55,
            'Sugar': 65
        }
        self.used_cards = {}
        self.scores = {}
        self.initialize_game()

    def initialize_game(self):
        # Sets up or resets the game environment
        self.used_cards = {}
        self.scores = {name: 0 for name in self.scores}  # Reset scores if names are already known
        print("Game has been initialized. Ready to play!")

    def pitcards(self):
        # Logic for handling card distribution and tracking used cards
        pass

    def pitplayers(self):
        # Initializes player scores based on player count
        self.scores = {}  # Clear previous player scores
        for _ in range(self.playercount):
            player_name = input("Enter player name: ")
            self.scores[player_name] = 0

    def pit_score(self, playername, card, bull=False):
        card_value = self.available_cards.get(card, 0)
        if bull:
            card_value *= 2
        self.scores[playername] += card_value
        self.check_win()

    def pit_bear(self, playername, bull=False):
        deduction = 40 if bull else 20
        self.scores[playername] = max(0, self.scores[playername] - deduction)

    def check_win(self):
        for player, score in self.scores.items():
            if score >= self.scoretowin:
                print(f"{player} has won the game with {score} points!")

    def pit_round(self):
        for player, score in self.scores.items():
            print(f"{player}: {score}")

    def new_game(self):
        # Resets all game information and starts a new game
        self.initialize_game()
        print("New game started. All settings have been reset.")

