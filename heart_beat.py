import random #Import for Simulation of Heart Beating
import time #Import this for Better Simulation, idk, just for timing 

def get_heart_rate(): #The Heart Rate Point
    return random.randint(68, 82) #The Simulation

try:
    while True: #While it means, that process is going all the time
        bpm = get_heart_rate() 
        print(f"HeartRate: {bpm} bpm", flush=True)#Import the Heart Beat on a Terminal
        time.sleep(1) #Timing

except KeyboardInterrupt: #CTRL + C for the OFF (if programm is running in Terminal VSCode for test)
    print("\nProgramm is OFF.") #Import on a VSCode Terminal
