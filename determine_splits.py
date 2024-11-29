# Helper function to determine the split based on the date
def determine_split(date):
    month, day, year = map(int, date.split('/'))
    return "Split1" if 1 <= month <= 6 else "Split2"