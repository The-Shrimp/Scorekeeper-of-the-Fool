# Scorekeeper-of-the-Fool a Discord Bot for Score Tracking and MySQL Integration

This Discord bot is designed to manage player scores, handle dynamic and frequently updated CSV files, and integrate with a MySQL database. The bot supports commands for updating scores, displaying leaderboards, and providing player statistics.

## Features

- **Introduction Command**: Sends an introduction message.
- **Update Score Command**: Updates the score of a player.
- **Scoreboard Leaders Command**: Displays the leading and runner-up players.
- **Scoreboard Command**: Displays the scoreboard for a specified year and split.
- **Player Stats Command**: Displays statistics for a specified player, year, and split.
- WIP **Upload CSV Command**: Uploads a CSV file and updates the MySQL database.

## Prerequisites

- Python 3.8 or higher
- Discord bot token
- MySQL database
- Required Python packages: `discord.py`, `mysql-connector-python`, `pandas`, `python-dotenv`

## Setup

1. **Clone the Repository**

   ```bash
   git clone https://github.com/The-Shrimp/Scorekeeper-of-the-Fool.git
   cd Scorekeeper-of-the-Fool
   ```
