import pyautogui
import time

try:
    while True:
        # Pega a posição atual X e Y
        x, y = pyautogui.position()
        # Formata a mensagem
        posicao = f"X: {x} | Y: {y}"
        print(posicao, end="\r")
        
        # Espera 0.5 segundo
        time.sleep(0.5)
        
except KeyboardInterrupt:
    print("\nEncerrado.")
