from datetime import datetime
from time import sleep
import pyautogui as pg
import pyperclip


def abrir_notepad():
  # Abre o Bloco de Notas apenas uma vez

  pg.press('win')
  sleep(1)
  pg.write('notepad')
  sleep(1)
  pg.press('enter')
  sleep(2)  # Tempo para o Notepad carregar totalmente
  print('Bloco de Notas aberto e pronto.')


def registrar_logs():
  # Apenas digita o log na janela que já está aberta na tela
  
  # 1. Captura a data e hora atualizada
  atual_data = datetime.now().strftime('%d/%m/%Y %H:%M:%S')
  texto_final = f'Atualizando as TVs {atual_data}'

  # 2. Copia e cola na janela ativa do Bloco de Notas
  pyperclip.copy(texto_final)
  pg.hotkey('ctrl', 'v')

  # 3. Pula para a próxima linha
  pg.press('enter')

  # 4. Espera o tempo determinado (ajuste se achar necessário)
  sleep(5)


# --- EXEMPLO DE EXECUÇÃO NO MESMO ARQUIVO ---
if __name__ == '__main__':
  # Abre o Bloco de Notas apenas UMA vez
  abrir_notepad()

  # Executa a digitação 3 vezes seguidas no MESMO bloco de notas
  registrar_logs()
  registrar_logs()
  registrar_logs()
