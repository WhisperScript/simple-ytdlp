# ytdl — yt-dlp Frontend (macOS / Linux / Windows)

## Fertige Programme (für Freunde)

Unter **Releases** gibt es je ein Programm pro System — nichts installieren, entpacken, starten:

| System | Datei | Start |
|---|---|---|
| Windows | `ytdl-windows.zip` | `ytdl.exe` doppelklicken. Bei der SmartScreen-Warnung „Weitere Informationen → Trotzdem ausführen". |
| macOS (Apple Silicon) | `ytdl-macos.zip` | Entpacken, **Rechtsklick auf ytdl.app → Öffnen**. Falls es blockiert wird: `xattr -dr com.apple.quarantine ytdl.app` |
| Linux | `ytdl-linux.tar.gz` | `tar xzf ytdl-linux.tar.gz && ./ytdl` |

Beim ersten Start lädt das Programm yt-dlp und deno automatisch herunter (Internet nötig,
wenige Sekunden). ffmpeg ist eingebaut. Mit dem Knopf **„yt-dlp aktualisieren"** bleibt es
aktuell — das hilft, wenn YouTube mal etwas ändert und Downloads plötzlich fehlschlagen.
Einstellungen und die Tools liegen im Nutzerordner (Windows `%LOCALAPPDATA%\ytdl`,
macOS `~/Library/Application Support/ytdl`, Linux `~/.local/share/ytdl`).

Die Programme sind nicht signiert, daher die Warnungen beim ersten Start. Intel-Macs nutzen
den `uv`-Weg unten.

### Selbst bauen / Release veröffentlichen

Der Workflow [.github/workflows/build.yml](.github/workflows/build.yml) baut alle drei
Systeme automatisch. Repo auf GitHub hochladen, dann:

    git tag v1.0 && git push --tags

Nach wenigen Minuten steht unter **Releases** alles zum Download bereit. Manuell startbar
über **Actions → Build → Run workflow** (Ergebnis als Artifact). Lokal, z. B. unter Windows:

    pip install pyinstaller imageio-ffmpeg certifi
    pyinstaller --onefile --windowed --name ytdl --collect-all imageio_ffmpeg ytdl.py

## Als Skript

Ein einzelnes Skript. Mit [uv](https://docs.astral.sh/uv/) braucht der Zielrechner
**nichts** außer uv selbst — Python, yt-dlp, ffmpeg und die JS-Engine holt es sich beim
ersten Start und legt sie in einen Cache, nicht ins System.

## Aufsetzen auf einem neuen Rechner

1. uv installieren:

   macOS / Linux:

       curl -LsSf https://astral.sh/uv/install.sh | sh

   Windows (PowerShell):

       powershell -c "irm https://astral.sh/uv/install.ps1 | iex"

2. `ytdl.py` rüberkopieren.
3. Starten:

       uv run ytdl.py

Das war's. Ohne Argumente öffnet sich die Oberfläche. Auf macOS/Linux geht nach
`chmod +x ytdl.py` auch der Direktstart `./ytdl.py` — die Shebang-Zeile ruft uv auf.

## Benutzung

Oberfläche: URLs untereinander einfügen, Modus wählen, Start. Läuft im Hintergrund,
Abbrechen jederzeit möglich, Log im Fenster.

Kommandozeile:

    uv run ytdl.py "https://youtube.com/watch?v=XXXX"
    uv run ytdl.py -a urls.txt -m mp3 -o ~/Music --archive
    uv run ytdl.py URL --mode video1080 --subs --thumb --by-uploader

Modi: `video` (beste Qualität), `video1080`, `video720`, `mp3`, `m4a`, `opus`.

Unbekannte Flags gehen unverändert an yt-dlp weiter:

    uv run ytdl.py URL --playlist-items 1-5
    uv run ytdl.py URL --simulate          # nur prüfen, nichts laden

Standard-Zielordner ist `~/Downloads/yt-dlp`. Fehlgeschlagene URLs landen dort in
`failed.log`; mit `--archive` merkt sich `archive.txt` bereits geladene Videos, ein
erneuter Lauf derselben Liste holt dann nur Neues nach (gut für Cron).

Für private oder altersbeschränkte Videos: `--cookies-from-browser chrome` bzw. im
Fenster das Browser-Dropdown.

## Ohne uv

Läuft mit vorhandenem Python: `pip install imageio-ffmpeg certifi`, dann `python3 ytdl.py`.
yt-dlp und deno lädt das Skript beim ersten Start selbst (`--update` aktualisiert yt-dlp).
Ein bereits installiertes ffmpeg wird bevorzugt, sonst greift das mitgelieferte.
Unter Linux ggf. `sudo apt install python3-tk` für die Oberfläche.

## Was automatisch passiert

- **ffmpeg**: System-ffmpeg wird bevorzugt; fehlt es, greift ein mitgeliefertes Binary
  (`imageio-ffmpeg`). Dem fehlt ffprobe — für Standardfälle wie mp3-Extraktion und
  Video-Merging reicht es, getestet.
- **JS-Engine**: YouTube braucht inzwischen eine, sonst fehlen hochauflösende Formate.
  Das Skript lädt deno beim ersten Start selbst; ein vorhandenes deno/node/bun wird sonst genutzt.
- **Playlists**: In der Oberfläche ist „Nur einzelnes Video" standardmäßig an, damit eine
  Video-URL mit `&list=…` nicht die ganze Playlist zieht. Abschalten für echte Playlists.
- **Windows**: keine aufblitzenden Konsolenfenster der Unterprozesse.
