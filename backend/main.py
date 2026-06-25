# main.py
from gui.gui import launch_game

def main():
    print("Welcome to Connect 4!")
    print("Choose mode:")
    print("1 - Play vs Our AI")
    print("2 - Play vs Online AI")

    choice = input("Enter 1 or 2: ").strip()

    if choice == "1":
        launch_game(ai_type="local")
    elif choice == "2":
        launch_game(ai_type="online")
    else:
        print("Invalid choice.")

if __name__ == "__main__":
    main()
