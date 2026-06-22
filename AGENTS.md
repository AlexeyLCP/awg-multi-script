# AGENTS.md — AwgToolza: LucX Edition

База знаний для работы с репозиторием `AlexeyLCP/awg-multi-script` (форк pumbaX/awg-multi-script).
VPN-менеджер AmneziaWG 2.0 с DPI-обходом, Xray, Warp, Каскадом, Telegram-ботом и веб-дашбордом.

## Окружение

- OS: Windows (разработка), продакшен: Ubuntu 24 / Debian 12+
- Shell в репо: bash (скрипты выполняются на Linux-серверах)
- Python: 3.13 (разработка), 3.x на серверах
- Git remote: `git@github.com:AlexeyLCP/awg-multi-script.git` (origin), `pumbaX/awg-multi-script` (upstream)
- Текущая версия: v6.9.3-lucx (см. CHANGELOG.md)

## Команды проверок

```bash
# Синтаксис bash (через WSL, т.к. нативный bash -n на Windows не читает C:\-пути)
bash -n /mnt/c/Users/dante/OneDrive/projects/AwgToolza/awg2.sh
bash -n /mnt/c/Users/dante/OneDrive/projects/AwgToolza/awg-bot-install.sh

# Синтаксис Python
python -m py_compile dashboard.py dashboard_dev.py run_speedtests_seq.py
# AST-парс (альтернатива)
python -c "import ast; [ast.parse(open(f,encoding='utf-8').read(), f) for f in ['dashboard.py','dashboard_dev.py','run_speedtests_seq.py']]"

# shellcheck НЕ установлен; pyflakes НЕ установлен. Доступны только bash -n и py_compile.
```

## Структура проекта

### Исходные файлы (в git)

| Файл | Строк | Назначение |
|---|---:|---|
| `awg2.sh` | 11164 | Главный bash-скрипт: меню, установка AWG-сервера, клиенты, Warp/Xray/Каскад/tun2socks/DNS, self-update. `set -euo pipefail` |
| `awg-bot-install.sh` | 3767 | Установщик Telegram-бота. Содержит встроенный Python-код бота в heredoc `'PYEOF'` (строки ~432-3588). `set -euo pipefail` |
| `dashboard.py` | 1450 | Веб-дашборд v1 (порт 8050). Устарел — заменён на dashboard_dev.py. `HTTPServer` single-threaded, без auth |
| `dashboard_dev.py` | 4131 | Веб-дашборд v2 (порт 8060). Новая версия: chains, add-node, proxy-config, deploy-chain, i18n. Загружает `nodes_config.json` |
| `run_speedtests_seq.py` | 76 | Запуск спидтестов последовательно по серверам (вызывается из дашборда) |
| `README.md` | — | Документация (RU) |
| `CHANGELOG.md` | — | Чейнджлог LucX Edition |
| `LICENSE` | — | MIT (база) + PolyForm NC (LucX доработки) |

### Временные файлы (НЕ в git, не аудировать)

- `*.conf` (client_*, client2_*, PCH_*) — клиентские конфиги AWG, генерируются awg2.sh
- `nodes_config.json` — конфиг нод для dashboard_dev.py (генерируется/редактируется через UI)
- `*_debug.log`, `claude_debug.log`, `new_claude_debug.log` — логи
- `__pycache__/` — кеш Python

### Ключевые пути на сервере (упоминаются в awg2.sh)

- `/etc/amnezia/amneziawg/awg0.conf` — серверный конфиг AWG (`SERVER_CONF`)
- `/root/<имя>_awg2.conf` — клиентские конфиги (хардкод `/root/`)
- `/etc/xray/config.json` — конфиг Xray (`XRAY_CONF`)
- `/etc/awg2-cascade/rules` — правила Каскада (`CASCADE_RULES`)
- `/usr/local/bin/awg-bot.py` — код бота (`BOT_PY`)
- `/etc/awg-bot.conf` — токен/chat_id бота (`BOT_CONF`, chmod 600)
- `/etc/systemd/system/awg-bot.service` — сервис бота (`BOT_SERVICE`)
- `~/awg_backup/` — бекапы (`BACKUP_DIR = ${REAL_HOME}/awg_backup`)

## Архитектура

### awg2.sh (главный скрипт)
- `set -euo pipefail` (строка 2)
- Глобальные константы путей вверху (строки 10-170)
- Хелперы: `safe_read` (65), `read_choice` (90), `read_yesno` (121), `hdr`/`ok`/`warn`/`err`/`info`
- Меню: `do_main_menu` → подменю (Сервер/Клиенты/Диагностика/Бекапы/Туннели/Бот/Удаление/Update)
- `set +e`/`set -e` тогглинг в меню-функциях (9 мест)
- CLI-флаги: `--auto`, `--add-client <имя>`, `--interactive`
- Профили мимикрии: Lite/Standard/Pro (TLS/DNS/SIP/QUIC), CPS-генератор I1-I5
- `do_self_update` — скачивает новый awg2.sh с GitHub `main` (проверка: размер >50KB, `bash -n`)
- Trap: `_global_cleanup` на EXIT, `set -e; echo "Прервано"` на INT/TERM (строки ~11103-11104)

### awg-bot-install.sh
- `self_update_installer` (58-89) — скачивает + `bash -n` + `bash "$tmp"` (нет checksum)
- `do_install_bot` (275+) — apt-get install python3/pip/qrencode, pip install python-telegram-bot, генерация `/usr/local/bin/awg-bot.py` (heredoc), `/etc/awg-bot.conf`, systemd unit
- `do_update_bot` (243-270) — читает старый токен/chat_id, ставит `SKIP_DEPS=1`, вызывает `do_install_bot`
- Встроенный Python-бот (432-3588): asyncio + python-telegram-bot v20, `run()` helper с `subprocess.run(timeout=30)`, `get_clients`/`get_live_stats`/`add_client`/`warp_*`, watchdog (`client_watchdog`), expire, notes, `fcntl.flock` на `/run/awg-bot.lock`

### dashboard.py (старый) vs dashboard_dev.py (новый)
- dev — НЕ строгий надмножество: API-контракт `/api/status` отличается (dev оборачивает в `{statuses, active_gateway, active_exits}`)
- dev: `load_config()`/`save_config()`/`reload_servers()` с `nodes_config.json`, `config_lock` (только в `reload_servers`)
- dev: chains (deploy-chain), add-node, proxy-config, deploy-proxy-routing, generate-client-config
- Оба: `HTTPServer` single-threaded, `0.0.0.0`, без auth, `AutoAddPolicy`, `paramiko`

## Найденные баги и исправления (аудит+фикс 2026-06-21)

Синтаксис чист везде (`bash -n` exit 0, `py_compile` OK). Применены фиксы для всех найденных багов.

### CRITICAL (ИСПРАВЛЕНО)

**awg2.sh:7141-7143** — `do_clean_clients` крашится под `set -e` при 0 пиров.
`client_count=$(grep -c "^\[Peer\]" "$SERVER_CONF" 2>/dev/null || echo "0")` → `"0\n0"` (grep печатает 0 И выходит 1 → fallback echo тоже печатает 0). Затем `[[ "0\n0" -eq 0 ]]` → "integer expression expected" → `set -e` убивает скрипт.
Воспроизведено: `bash /mnt/c/Temp/opencode/test_grep.sh` → `Case1 x=[0\n0]`.
**Фикс (применён):** `client_count=$(grep -c "^\[Peer\]" "$SERVER_CONF" 2>/dev/null || true); [[ "$client_count" =~ ^[0-9]+$ ]] || client_count=0`

**awg2.sh:2440 + 2463** — `_share_config` крашится для конфигов без I1-I5 (Lite/basic).
`has_i1=$(grep -cE "^I[1-5] = " "$conf_file" 2>/dev/null || echo 0)` → `"0\n0"`, `[[ "0\n0" -gt 0 ]]` → краш. Пользователь не видит конфиг/QR.
**Фикс (применён):** тот же паттерн `|| true` + regex-валидация.

**awg-bot-install.sh:58-89** — self-update выполняет скачанный скрипт как root без checksum/signature, с `main` ветки (не pinned tag). Только `bash -n`. Компрометация GitHub/MITM → root RCE на всех серверах.
**Фикс (применён):** добавлена проверка SHA256 (загружается `.sha256` рядом со скриптом), при недоступности — интерактивный запрос подтверждения.

**dashboard_dev.py:4110 + 3412-3425** — `HTTPServer(('0.0.0.0', 8060))` без auth + POST `/api/proxy-config` принимает любой JSON без валидации → пишется в `nodes_config.json` → деплоится на серверы через SSH. Remote root-equivalent.
**Фикс (применён):** добавлена аутентификация по Bearer token (`secrets.token_hex(16)`, автогенерация в `nodes_config.json`), `ThreadingHTTPServer`, валидация proxy-config через `_validate_proxy_config()` (проверка типов, regex для domain, hex-only для secret).

**dashboard_dev.py:3982** — TOML-инъекция: `users_toml += f'{u["name"]} = "{u["secret"]}"\n'` с user-controlled name/secret.
**Фикс (применён):** `_toml_escape()` для всех значений, валидация name (`^[a-zA-Z0-9_]+$`) и secret (`^[a-fA-F0-9]+$`) в `_validate_proxy_config()`.

### HIGH (ИСПРАВЛЕНО)

**awg2.sh:9162+9919 (и 6085+5390, 11062+11058)** — nested `set -e` clobbering.
Меню-функция делает `set +e`, вызывает подменю, которое в конце делает `set -e` → `set -e` остаётся внутри родительского `while` цикла. Последующий `read -rp` с EOF/Ctrl-D убивает родительское меню.
**Фикс (применён):** `set +e` после каждого вложенного вызова меню (6 мест в `show_submenu_5` + 3 вызова submenu).

**awg2.sh:2831 & 3032** — `do_autoinstall` и `do_add_client_noninteractive` хардкодят `SERVER_REGION="ru"` + RU-пулы доменов, игнорируя реальный регион сервера. На world-сервере `--add-client` генерирует I1 TLS с ya.ru/vk.com (недоступны из EU) → пустой CPS → клиент без I1.
**Фикс (применён):** новая функция `_detect_server_region()` читает `# Region:` из `$SERVER_CONF` и устанавливает пулы; `do_autoinstall`/`do_add_client_noninteractive` вызывают её вместо хардкода "ru".

**awg2.sh:9529** — Python-инъекция в `_xray_remove_outbound`.
`python3 -c "...o.get('tag') != '$target'..."` где `$target = "proxy_" + hostname.replace('.','_')` из user-pasted vless-ссылки. Hostname с `'` ломает Python-строку → RCE as root.
**Фикс (применён):** передача `$target` через `sys.argv[1]`.

**awg-bot-install.sh:263** — `SKIP_DEPS=1` ставится в `do_update_bot`, но `do_install_bot` НИКОГДА его не проверяет → apt-get/pip переустанавливаются на каждом обновлении. Флаг — мёртвый код.
**Фикс (применён):** обёртка строк 297-308 в `if [[ "${SKIP_DEPS:-0}" != "1" ]]; then ... fi`.

**awg-bot-install.sh:565-571** — `run()` использует `subprocess.run(timeout=30)` без ловли `TimeoutExpired`.
**Фикс (применён):** `try: ... except subprocess.TimeoutExpired: return (-1, "", "timeout")` + `except FileNotFoundError`.

**awg-bot-install.sh:418-425** — токен бота пишется в `/etc/awg-bot.conf` через `cat >` ДО `chmod 600`. Окно с umask 0644 → токен world-readable.
**Фикс (применён):** `( umask 077; cat > "$BOT_CONF" )` — файл создаётся сразу с правами 600.

**awg-bot-install.sh:414** — `cp "$BOT_CONF" "$BOT_CONF.bak.$(date +%s)"` без `-p` → backup с 0644. Без ротации.
**Фикс (применён):** `cp -p` + `find /etc -maxdepth 1 -name 'awg-bot.conf.bak.*' -mtime +30 -delete`.

**dashboard_dev.py:318-321, 329-380** — утечки ресурсов. `socket.socket()` без `with`/`try-finally`. `ssh = get_ssh_client()` без `try-finally`.
**Фикс (применён):** `with contextlib.closing(socket.socket()) as s:` и `try: ssh = ...; ... finally: if ssh: ssh.close()` (применено к `collect_server_status`, `get_active_proxy_gateway`, `get_proxy_monitor_logs`, `get_active_exit_interfaces`).

**dashboard_dev.py:346** — command injection: `f"systemctl is-active {svc}"` где `svc` из `nodes_config.json`.
**Фикс (применён):** `shlex.quote(str(svc))` + валидация `^[a-zA-Z0-9@._-]+$`.

**dashboard_dev.py:3634, 3669, 3677-3678, 3754, 3801** — command injection: chain/proxy deploy строит shell-команды из `node['name']` без `shlex.quote`.
**Фикс (применён):** `re.sub(r'[^a-zA-Z0-9_]', '_', name)` для slug-генерации + `shlex.quote()` для всех путей в `tee`/`systemctl`/`awg2` командах.

**dashboard_dev.py:3928-3929, 4017, 4021** — TOML-инъекция: `port`, `domain` из POST body без type-check/escaping.
**Фикс (применён):** `int(port)` с bounds check, `_toml_escape(domain)`, валидация domain через regex.

**dashboard_dev.py:3324, 3440-3455, 3498-3708, 3710-4059** — single-threaded `HTTPServer` + долгие SSH-операции блокируют ВСЕ клиенты.
**Фикс (применён):** `ThreadingHTTPServer` вместо `HTTPServer`.

**dashboard_dev.py:97-160, 3376-3417** — `load_config()` read-modify-write без `config_lock` в POST handlers.
**Фикс (применён):** все POST RMW обёрнуты в `with config_lock:`.

### MEDIUM (ИСПРАВЛЕНО)

**awg2.sh:1564** — `do_repair`: тот же `grep -c || echo "0"` → `"0\n0"`. Ложное "Расхождение".
**Фикс (применён):** `|| true` + regex-валидация.

**awg2.sh:5952** — `_warp_status`: `"00 из 0 клиент(ов)"`.
**Фикс (применён):** `|| true` + существующий `tr -d` теперь работает корректно.

**awg2.sh:7275** — `do_restore`: `find ... | sort -r` без `-print0`.
**Фикс (применён):** `-print0` + `sort -rz` + `read -r -d ''`.

**awg2.sh:8037** — cascade `comment="${comment//|/ }"` — только `|`, не newlines/control chars.
**Фикс (применён):** также strip `\n`, `\r`, `\t`.

**awg2.sh:9718-9725** — `_xray_up`: iptables `-A` не проверяется.
**Фикс (применён):** явные `if !` проверки с `warn` при ошибке.

**awg2.sh:5093-5094** — `ip rule add` не проверяется.
**Фикс (применён):** `if ! ip rule add ... 2>/dev/null; then warn ...`.

**awg2.sh:9887** — `> "$XRAY_PEERS"` truncate под `set +e` swallowит ошибку.
**Фикс (применён):** `if ! : > "$XRAY_PEERS" 2>/dev/null; then warn ...`.

**awg-bot-install.sh:298, 304-305** — `2>/dev/null` скрывает реальную ошибку apt/pip.
**Фикс (применён):** повторный `apt-get install 2>&1 | tail -20 >&2` в обработчике ошибки.

**awg-bot-install.sh:1761-1763** — `_cb_restart`: `awg-quick down` return code/stderr discarded.
**Фикс (применён):** сохранение `down_rc`/`down_err`, вывод в сообщении об ошибке.

**awg-bot-install.sh:247-248** — `OLD_TOKEN`/`OLD_CHAT_ID` без `local`.
**Фикс (применён):** `local OLD_TOKEN OLD_CHAT_ID`.

**dashboard_dev.py:3585-3586** — добавление proxy-узла ставит `["vpn-route-monitor"]`, теряя `mtproxymax`.
**Фикс (применён):** `["vpn-route-monitor", "mtproxymax"]`.

**dashboard_dev.py:3617** — single-hop chain: `range(0,0,-1)` = пусто → 0 туннелей, но цепочка "deployed".
**Фикс (применён):** валидация `len(hops) >= 2` в POST `/api/chains` и guard в deploy-chain.

**dashboard_dev.py:184, 188, 336, 3240** — bare `except:` ловит `KeyboardInterrupt`/`SystemExit`.
**Фикс (применён):** `except Exception:`.

**dashboard_dev.py:3527** — `escaped_key` только экранирует `"`, не backticks/`$()`.
**Фикс (применён):** валидация формата ключа `^ssh-(ed25519|rsa) [A-Za-z0-9+/=]+$` + single-quote в shell.

### LOW (ИСПРАВЛЕНО)

- `dashboard_dev.py:2594-2606` — `is_handshake_recent` regex: `"5min"` (без пробела) не матчится → возвращает True.
  **Фикс (применён):** `r'(\d+)\s*(?:min|minute)'` — `\s*` вместо `\s+`.
- `run_speedtests_seq.py:6` — хардкод `C:\Users\dante\.ssh\id_ed25519`.
  **Фикс (применён):** `$SSH_KEY_PATH` env var + `USERPROFILE` для Windows.
- `run_speedtests_seq.py:31` — `AutoAddPolicy` (MITM).
  **Фикс (применён):** `load_host_keys(known_hosts)` перед `AutoAddPolicy` (fallback).
- `run_speedtests_seq.py:66` — "11 servers" в логе, но 5 entries.
  **Фикс (применён):** `len(servers)`.

### Не исправлено (LOW / by design)

- `awg2.sh:2476` — `. /etc/os-release` clobberит globals (`VERSION` сохранён через `_SAVED_VERSION`, `PRETTY_NAME`/`ID` утекают) — низкий риск, `local` для используемых vars
- `awg2.sh:10251` — `eval "$route_cmd"` (в генерируемом скрипте, не runtime) — iface имена от kernel, безопасно
- `awg2.sh:1386` — `rand_range` интерполирует vars в Python source — безопасно сейчас, хрупко
- `awg-bot-install.sh:3583` — `run_polling(timeout=30)` — валидный параметр PTB v20+
- `dashboard_dev.py:101, 104-113, 3923` — хардкод MTProto-секретов в `default_proxy`/fallback — by design (дефолтные секреты для первого запуска, заменяются через UI)
- `dashboard_dev.py:3525-3527, 3635, 3755, 4090` — `exec_command` без `read()` (fire-and-forget) — может вызвать deadlock буфера при больших выводах, но текущие команды дают малый вывод
- `dashboard_dev.py:3635, 3755, 4090` — `time.sleep(2)` вместо `recv_exit_status()` — компромисс между сложностью и надёжностью

## Топ-5 багов для приоритетного фикса

1. ✅ **awg2.sh:7141-7143** — `grep -c || echo "0"` → `"0\n0"` крашит `do_clean_clients` под `set -e`
2. ✅ **awg2.sh:2440+2463** — тот же паттерн крашит `_share_config` для Lite-клиентов
3. ✅ **awg-bot-install.sh:58-89** — self-update без checksum = root RCE
4. ✅ **dashboard_dev.py:4110+3412-3417+3982** — no-auth дашборд + невалидируемый POST + TOML-инъекция
5. ✅ **awg2.sh:9529** — Python-инъекция через vless hostname → root RCE

## Конвенции кода

- bash: `[[ ]]` везде (не `[ ]`), `local` в функциях (но не для всех globals), `safe_read`/`read_choice`/`read_yesno` хелперы с `-r`
- Цвета: `${Y}` `${R}` `${G}` `${N}` `${C}` `${W}` `${D}` (определены вверху awg2.sh)
- Python dashboard: `paramiko` для SSH, `subprocess` для локальных команд, `json` для конфигов, без фреймворков (голый `http.server`)
- Сообщения пользователя на русском
- Коммиты: `fix(scope): ...` / `feat: ...` / `docs: ...` (см. `git log --oneline`)

## Чек-лист перед коммитом

1. `bash -n awg2.sh && bash -n awg-bot-install.sh` (через WSL)
2. `python -m py_compile dashboard.py dashboard_dev.py run_speedtests_seq.py`
3. Не коммитить `*.conf`, `nodes_config.json`, `*_debug.log`, `__pycache__/`
4. Проверить `git status` — staged только intended файлы
5. Сообщение коммита в стиле существующих (см. `git log --oneline -10`)