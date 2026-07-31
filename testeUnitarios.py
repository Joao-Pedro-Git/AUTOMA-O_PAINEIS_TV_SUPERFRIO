from datetime import datetime
from time import sleep
import pyautogui as pg
import pyperclip
import pygetwindow as gw

# AJUSTE AQUI: O Windows em português usa "Bloco de Notas" no título da janela
nome_arquivo = "Bloco de Notas"
GLOBAL_SLEEP_TIME = sleep(4)  # Tempo de espera padrão entre ações
# FUNÇÕES 
def abrir_notepad():
    pg.press('win')
    GLOBAL_SLEEP_TIME
    pg.write('notepad')
    GLOBAL_SLEEP_TIME
    pg.press('enter')
    GLOBAL_SLEEP_TIME  # Tempo para o Notepad carregar totalmente
    print('Bloco de Notas aberto e pronto.')

def registrar_logs():
    # Movido para cá para capturar a hora exata do registro do log
    atual_data = datetime.now().strftime('%d/%m/%Y %H:%M:%S')
    texto_final = f'Atualizando as TVs {atual_data}'
    
    # Copia e cola na janela ativa do Bloco de Notas
    pyperclip.copy(texto_final)
    pg.hotkey('ctrl', 'v')
  
    # Pula para a próxima linha
    pg.press('enter')
    print(f'Log registrado: {texto_final}')
    GLOBAL_SLEEP_TIME

def main():
    # Buscar as janelas pelo título correto
    janelas = gw.getWindowsWithTitle(nome_arquivo)

    if not janelas:
        # SE NÃO houver janelas abertas, abre uma nova
        print("Bloco de Notas não encontrado. Abrindo...")
        abrir_notepad()
        # Espera um momento e busca novamente após abrir
        sleep(1)
        janelas = gw.getWindowsWithTitle(nome_arquivo)
    
    # Se a janela existe (ou acabou de ser aberta)
    if janelas:
        try:
            # Seleciona a primeira janela encontrada na lista
            janela_notepad = janelas[0]
            janela_notepad.activate()
            sleep(1) 
        except Exception:
            # Caso a janela esteja minimizada, restaura primeiro
            janela_notepad.restore()
            janela_notepad.activate()
            sleep(1)

        registrar_logs()

if __name__ == '__main__':
    main()
