# MP3 Downloader

Baixa áudio de URLs ou playlists (YouTube e +1800 sites, via [yt-dlp](https://github.com/yt-dlp/yt-dlp)),
converte para **MP3 192 kbps** com metadados e capa embutida, e entrega **um único `.zip`** em `output/`.

Roda inteiramente dentro do Docker — você não precisa instalar Python, yt-dlp nem ffmpeg na máquina.

---

## Começando

```bash
./dl.sh 'https://www.youtube.com/watch?v=jNQXAC9IVRw'
```

Na primeira vez o script constrói a imagem (alguns minutos). Depois é instantâneo.
O `.zip` aparece em `output/`, já com o seu usuário como dono.

---

## Os três jeitos de usar

### 1. Uma URL avulsa

```bash
./dl.sh 'https://www.youtube.com/watch?v=ID'
```

> **Sempre entre aspas simples.** URLs têm `&` e `?`, que o shell interpreta.

### 2. Uma playlist ou canal inteiro

```bash
./dl.sh 'https://www.youtube.com/playlist?list=PLxxxxxxxx'
./dl.sh 'https://www.youtube.com/@canal/videos'
```

Uma playlist é uma entrada só que expande em N faixas — todas vão para o mesmo `.zip`.

Se o link for de um vídeo **dentro** de uma playlist (`watch?v=X&list=Y`), por padrão
ele baixa a playlist inteira. Para pegar só aquele vídeo:

```bash
./dl.sh --no-playlist 'https://www.youtube.com/watch?v=X&list=Y'
```

### 3. Um arquivo `.txt` com uma URL por linha

Crie o arquivo em `input/`:

```
# input/minhas_musicas.txt
# linhas com # são comentários, linhas vazias são ignoradas
https://www.youtube.com/watch?v=aaaaaaaaaaa
https://www.youtube.com/watch?v=bbbbbbbbbbb
https://www.youtube.com/playlist?list=PLxxxxxxxx
```

E rode — o nome do arquivo basta, não precisa do caminho:

```bash
./dl.sh minhas_musicas.txt
```

URLs repetidas são ignoradas automaticamente (o resumo diz quantas).
Você também pode misturar tudo numa chamada só:

```bash
./dl.sh lista1.txt lista2.txt 'https://www.youtube.com/watch?v=ID'
```

---

## O que sai

```
output/
└── mp3_2026-09-12_14-33-05.zip
```

Só o `.zip` — os `.mp3` soltos são apagados depois de empacotar. Dentro dele:

| Arquivo | O quê |
|---|---|
| `Título [id].mp3` | uma por faixa, 192 kbps, com tags e capa |
| `_relatorio.txt` | data, totais, e a URL de origem de cada faixa |
| `_falhas.txt` | só se algo falhou — as URLs que não deram certo |

O `_falhas.txt` é feito para ser realimentado: extraia e rode `./dl.sh _falhas.txt`
para tentar de novo só o que faltou.

---

## Opções úteis

```bash
./dl.sh --dry-run lista.txt          # lista o que baixaria, sem baixar nada
./dl.sh -b 320 'https://...'         # 320 kbps em vez de 192
./dl.sh --name trilha_sonora l.txt   # nomeia o zip: output/trilha_sonora.zip
./dl.sh --sleep 2 lista_grande.txt   # pausa 2s entre faixas (evita bloqueio)
./dl.sh --no-playlist 'https://...'  # ignora a playlist do link
./dl.sh --keep-files 'https://...'   # mantém os .mp3 soltos (depuração)
./dl.sh -v 'https://...'             # log detalhado quando algo dá errado
./dl.sh --help                       # todas as opções
```

**Antes de um lote grande, use `--dry-run`.** Ele valida um `.txt` de 200 linhas em
segundos, em vez de você descobrir um erro depois de 40 minutos baixando.

---

## Manutenção

```bash
make help        # lista os alvos
make build       # constrói a imagem
make version     # mostra as versões de yt-dlp e ffmpeg na imagem
make shell       # abre um shell dentro do container
make clean       # apaga os .zip de output/
```

### Quando parar de funcionar

O YouTube muda com frequência e o yt-dlp corre atrás. Quase todo erro estranho
(`Unable to extract`, `Sign in to confirm`) se resolve atualizando:

```bash
make update YTDLP_VERSION=              # pega a última versão estável
make update YTDLP_VERSION=2026.8.19     # ou fixa uma versão específica
make update-nightly                     # fix já mergeado mas ainda sem release
```

O rebuild leva ~10 segundos: a camada do ffmpeg fica em cache, só o pip roda de novo.
Para builds reprodutíveis, fixe a versão em `.env`:

```
YTDLP_VERSION=2026.8.19
```

### "Sign in to confirm you're not a bot"

O YouTube às vezes exige login. Exporte os cookies de um navegador logado
(extensão *Get cookies.txt*), salve em `input/cookies.txt` e passe:

```bash
./dl.sh --cookies input/cookies.txt lista.txt
```

---

## Códigos de saída

Úteis se você chamar isto de outro script:

| Código | Significado |
|---|---|
| `0` | tudo certo, `.zip` gerado |
| `1` | parcial: algumas faixas falharam, mas o `.zip` foi gerado |
| `2` | argumento inválido |
| `3` | nenhum download deu certo — **nenhum `.zip` é gerado** |
| `4` | entrada inválida: arquivo não existe ou sem URLs válidas |
| `5` | problema de ambiente: ffmpeg ausente, `output/` não gravável |
| `130` | interrompido com Ctrl-C (arquivos parciais são removidos) |

---

## Estrutura

```
downloader/
├── models.py     dataclasses compartilhadas (não importa yt_dlp)
├── errors.py     exceções tipadas -> exit codes / status HTTP
├── sources.py    token -> lista de URLs (.txt ou URL)
├── core.py       wrapper do yt-dlp — o único módulo que o importa
├── zipper.py     arquivos -> .zip atômico
├── pipeline.py   orquestra: resolve -> baixa -> empacota -> limpa
└── cli.py        argparse + terminal — o único que imprime
```

O núcleo é independente de interface: não imprime, não chama `sys.exit`, e emite
eventos de progresso em vez de desenhar na tela. Uma Web UI (FastAPI) chamaria as
mesmas funções — o esqueleto do serviço já está comentado no `docker-compose.yml`.

Rodar sem Docker é possível (`python main.py ...`), mas exige `ffmpeg` e `yt-dlp`
instalados no sistema.

---

## Nota legal

Baixar conteúdo protegido por direitos autorais pode violar os termos de uso dos
sites. A ferramenta existe para usos legítimos: conteúdo próprio, material sob
licença livre e arquivamento pessoal.
