import time
import pyautogui
import schedule
from testeUnitarios import registrar_logs

# FUNCÃO PARA ATUALIZAR AS TVS AUTOMATICAMENTE EM HORARIOS PRÉ-DEFINIDOS
from automação import executar_processo

HORARIO_ATUALIZACAO_ONE = "11:35:00"
HORARIO_ATUALIZACAO_TWO = "08:00:00"
HORARIO_ATUALIZACAO_THREE = "16:00:00"


def job():
  registrar_logs()
  executar_processo()
  

# Agendando para rodar todo dia às 08:00
schedule.every().day.at(HORARIO_ATUALIZACAO_ONE).do(job)

# Loop necessário para manter o script ativo
while True:
  schedule.run_pending()
  time.sleep(1)
