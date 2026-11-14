# Step 1: Import required libraries
import tkinter as tk
import math
import random  # Simulates heartbeat sensor readings


# Step 2: Simulate a heartbeat reading
def get_heart_rate():
    return random.randint(68, 82)


# Step 3: Create the points used to draw the heart
def make_heart_points(cx, cy, scale):
    points = []

    for i in range(100):
        t = 2 * math.pi * i / 100

        x = 16 * math.sin(t) ** 3
        y = (
            13 * math.cos(t)
            - 5 * math.cos(2 * t)
            - 2 * math.cos(3 * t)
            - math.cos(4 * t)
        )

        points.extend([
            cx + x * scale,
            cy - y * scale
        ])

    return points


# Step 4: Update the heartbeat display and console message
def update_display():
    if not window.winfo_exists():
        return

    bpm = get_heart_rate()

    # Update the number shown in the application window
    pulse_label.config(text=str(bpm))
    status_label.config(text="Receiving heartbeat data")

    # Print each heartbeat reading on a new line so updates are clearly visible in the terminal
    print(f"❤️ HeartRate: {bpm} bpm", flush=True)

    # Schedule the next update in 1000 milliseconds
    window.after(1000, update_display)


# Step 5: Stop the application
def stop_program():
    # Move to a new console line after the live message
    print()
    window.destroy()


# Step 6: Create and configure the application window
window = tk.Tk()
window.title("Astronaut Heartbeat")
window.geometry("400x440")
window.configure(bg="#101820")


# Step 7: Draw the heart
canvas = tk.Canvas(
    window,
    width=400,
    height=260,
    bg="#101820",
    highlightthickness=0
)
canvas.pack(pady=20)

heart_points = make_heart_points(cx=200, cy=135, scale=8)

canvas.create_polygon(
    heart_points,
    fill="#e63946",
    outline="#ff6b6b",
    width=3
)


# Step 8: Create the heartbeat number and unit labels
pulse_label = tk.Label(
    window,
    text="--",
    font=("Arial", 48, "bold"),
    fg="white",
    bg="#101820"
)
pulse_label.pack()

unit_label = tk.Label(
    window,
    text="Heart rate (BPM)",
    font=("Arial", 18),
    fg="#cbd5e1",
    bg="#101820"
)
unit_label.pack()


# Step 9: Create the status label
status_label = tk.Label(
    window,
    text="Waiting for data...",
    font=("Arial", 12),
    fg="#8ecae6",
    bg="#101820"
)
status_label.pack(pady=15)


# Step 10: Create the stop button
stop_button = tk.Button(
    window,
    text="Stop",
    font=("Arial", 14),
    bg="#e63946",
    fg="white",
    command=stop_program
)
stop_button.pack(pady=10)

# Step 11: Start continuous updates and run the application
update_display()
window.mainloop()