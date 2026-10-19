# CHDPS1 — Conversor de ROMs PS1 com múltiplas tracks

Ferramenta gratuita de conversão **CUE + BIN → CHD**, compatível com dumps de CD-ROM do PlayStation 1. O projeto usa o **CHDMAN original do MAME** para a conversão (não implementa um codec CHD próprio), adicionando pré-validação de CUE/índices/trilhas, ZIP e checagem do CHD final.

## Recursos

- Identifica **todas as TRACKs** de dados e áudio, incluindo `INDEX 00`, `INDEX 01` e pregaps do CUE.
- Aceita **ZIP** contendo `.cue` + vários `.bin`, inclusive dentro de uma subpasta, e extrai temporariamente os arquivos necessários.
- Verifica BINs ausentes, tamanhos/setores inválidos, numeração e índices das trilhas antes de converter.
- Executa `chdman createcd`, depois `chdman verify` e `chdman info`; compara a contagem de tracks quando o CHDMAN publica metadados legíveis.
- Gera o resultado em arquivo temporário e só o move para o destino **após validar**, sem apagar os arquivos originais.
- Interface gráfica (Windows/Linux) e linha de comando para automação. Não sobrescreve CHDs existentes sem permissão explícita.
- Não modifica suas ROMs e não inclui ROMs comerciais.

## Windows (interface gráfica)

**[Baixar CHDPS1 Windows completo (GitHub Releases)](https://github.com/danilostorm/chdps1/releases/latest)** — pacote portátil que inclui `CHDPS1.exe` (interface), `CHDPS1-CLI.exe` e o `chdman.exe` original do MAME 0.289. Basta extrair o ZIP e executar `CHDPS1.exe`. Nenhuma instalação adicional de CHDMAN é necessária no Windows. O programa é para Windows 10 ou posterior (x64, CPU x86-64-v2).

**CHDMAN:** o pacote completo da Release já inclui uma cópia do executável oficial do [MAME 0.289](https://www.mamedev.org/release.html) e arquivos auxiliares. Não é necessário procurá-lo manualmente. Os arquivos de código-fonte permanecem separados dos binários publicados na Release.

1. Clique em **Procurar** e selecione o ZIP da ROM ou o CUE.
2. Clique em **Analisar tracks** para conferir as pistas detectadas.
3. Escolha a pasta de destino; clique em **Converter e verificar**.
4. Será gerado **um arquivo CHD por disco** (não um arquivo por track).

> Ao abrir um `.cue` isolado, seus `.bin` devem estar na mesma pasta ou nos caminhos relativos definidos no CUE. Ao abrir um `.zip`, todos os arquivos referenciados devem estar dentro dele.

## Linha de comando

```bash
# Somente analisar (não precisa ter CHDMAN instalado):
python chdps1.py "Bakusou Dekotora Densetsu.zip" --analyze

# ZIP para CHD, validar automaticamente:
python chdps1.py "Jogo PS1.zip" -o ./convertidos

# CUE com muitas trilhas:
python chdps1.py "Jogo PS1.cue" --chdman /usr/bin/chdman

# Sobrescrever somente após concluir a verificação:
python chdps1.py "Jogo PS1.zip" -o ./convertidos --overwrite
```

Em Linux, instale o `chdman` do pacote MAME fornecido pela sua distribuição. Em Windows, use `chdman.exe` na pasta do executável ou no PATH.

## Compatibilidade e integridade

- Preferência por dumps `BIN/CUE` íntegros do PS1, incluindo discos mistos `MODE2/2352` e `AUDIO`. O programa **não converte cada BIN separadamente**: o CUE descreve o layout inteiro do CD.
- Áudio PCM em BIN binário é aceito; CUEs que usam MP3, FLAC ou WAV externos exigem tratamento adicional e são recusados para não produzir um CHD incompleto.
- `verify` valida a integridade armazenada no CHD; `info` verifica o layout de tracks quando os metadados são reportados. **Isso não garante, por si só, que um dump já danificado será reparado**, nem substitui um teste de reprodução no emulador.
- Conversão é sem perdas no conteúdo do CD, mas extração de CHD pode produzir uma organização de arquivos BIN diferente daquela de origem. Veja a [discussão do MAME](https://github.com/mamedev/mame/issues/5867).
- Não inclui implementação de emulador nem altera o núcleo do RetroArch.

### Exemplo real de análise

No jogo *Bakusou Dekotora Densetsu* enviado para teste, o analisador encontrou **3 tracks**: 01 `MODE2/2352`, 02 `AUDIO` com `INDEX 01 00:03:42`, e 03 `AUDIO` com `INDEX 01 00:03:70`. Os três BINs têm tamanho múltiplo de 2.352 bytes. O teste real de criação/abertura de CHD depende de executar um CHDMAN nativo do sistema.

## Testes

```bash
python -m unittest discover -s tests -v
```

A CI do GitHub Actions testa o parser, executa conversões reais sintéticas com o CHDMAN oficial do MAME, compila os executáveis Windows, monta o ZIP completo e publica a Release automaticamente. O teste da ROM comercial de exemplo não faz parte da CI. Para validar **a conversão verdadeira de um jogo**, rode com CHDMAN oficial e, de preferência, abra o CHD em um emulador.

## Créditos

CHD é formato desenvolvido pelo projeto [MAME](https://docs.mamedev.org/tools/chdman.html). Este projeto chama o executável oficial `chdman`. Referência de interface analisada: [CHDCreator](https://github.com/osamaishtiaq/CHDCreator). Não contém código copiado do CHDCreator ou de terceiros.