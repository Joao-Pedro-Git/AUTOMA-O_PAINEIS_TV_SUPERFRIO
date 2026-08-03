CORREÇÃO DO EXECUTÁVEL

O projeto gera dois executáveis:

    dist/
        LoopAtualizar.exe
        gerar_relatorio.exe

Não adicione a pasta utils inteira com --add-data.
Os módulos importados são incorporados ao LoopAtualizar.exe.
A automação Selenium é compilada separadamente.

PASSOS

1. Instale:
   python -m pip install -r requirements.txt

2. Configure as credenciais:
   setx PENTAHO_USUARIO "SEU_USUARIO"
   setx PENTAHO_SENHA "SUA_SENHA"

3. Feche e reabra o terminal após usar setx.

4. Compile:
   python criar_executaveis.py

5. Abra:
   dist\LoopAtualizar.exe

6. Compartilhe a pasta dist inteira.

ERRO ANTERIOR

O código possuía dois blocos de caminhos. O segundo sobrescrevia
o caminho sys._MEIPASS e obrigava o executável a procurar:

    dist\utils\gerar_relatorio.py

No modo final corrigido, ele procura:

    dist\gerar_relatorio.exe