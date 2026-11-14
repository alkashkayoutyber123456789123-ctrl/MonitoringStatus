"""Print a simple random heart-rate demo once per second."""

import random
import time


def main():
    # Это случайное число для демонстрации, настоящий датчик не используется.
    try:
        while True:
            bpm = random.randint(68, 82)
            print(f"Heart rate: {bpm} BPM", flush=True)
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nDemo stopped.")


# Запускаем цикл только если файл вызвали напрямую, а не импортировали.
if __name__ == "__main__":
    main()
