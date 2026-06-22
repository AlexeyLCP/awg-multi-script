# Чейнджлог / Changelog (LucX Edition)

## [v6.9.4-lucx] — 2026-06-22

### Безопасность (Security)

- **CRITICAL: Проверка SHA256 при self-update** — `do_self_update` (awg2.sh) и `self_update_installer` (awg-bot-install.sh) теперь проверяют SHA256-контрольную сумму скачанного файла. Файлы `.sha256` публикуются рядом со скриптами в репозитории. При недоступности `.sha256` запрашивается интерактивное подтверждение.
- **CRITICAL: Аутентификация веб-дашборда** — `dashboard_dev.py` теперь требует Bearer token для всех POST-запросов и чувствительных GET (`/api/proxy-config`, `/api/proxy-monitor-logs`). Токен автогенерируется (`secrets.token_hex(16)`) и сохраняется в `nodes_config.json`.
- **CRITICAL: Валидация proxy-config POST** — тело `/api/proxy-config` валидируется через `_validate_proxy_config()`: проверка типов, regex для domain (`^[a-zA-Z0-9._-]+$`), hex-only для secret (`^[a-fA-F0-9]+$`), int bounds для port (1-65535).
- **CRITICAL: TOML-экранирование** — все пользовательские данные (name, secret, domain) экранируются через `_toml_escape()` перед записью в TOML-конфиги MTProxy/MTG. Предотвращает TOML-инъекцию.
- **HIGH: Command injection** — `shlex.quote()` применяется ко всем сервисам/интерфейсам/именам в shell-командах dashboard. Валидация `^[a-zA-Z0-9@._-]+$` для service names.
- **HIGH: Python-инъекция устранена** — `_xray_remove_outbound` передаёт target через `sys.argv[1]` вместо интерполяции в Python source. `rand_range` — аналогично.
- **HIGH: Права на BOT_CONF** — токен бота создаётся сразу с правами 600 через `( umask 077; cat > "$BOT_CONF" )`. Раньше файл создавался с 644 и затем `chmod 600` — окно с world-readable токеном.
- **HIGH: Ротация бекапов BOT_CONF** — `cp -p` сохраняет права 600, старые бекапы удаляются через 30 дней (`find -mtime +30 -delete`).
- **MEDIUM: SSH `AutoAddPolicy`** — в `run_speedtests_seq.py` теперь `load_host_keys()` перед `AutoAddPolicy` (fallback для известных хостов).

### Исправления багов (Bug Fixes)

- **CRITICAL: Краш `do_clean_clients` при 0 пиров** — паттерн `grep -c ... || echo "0"` производил `"0\n0"` (grep печатает 0 И выходит 1 → fallback echo тоже печатает 0). Затем `[[ "0\n0" -eq 0 ]]` → "integer expression expected" → `set -e` убивал скрипт. **Фикс:** `|| true` + regex-валидация `[[ =~ ^[0-9]+$ ]] || var=0`. Применено к 4 местам (`do_clean_clients`, `_share_config`, `do_repair`, `_warp_status`).
- **CRITICAL: Краш `_share_config` для Lite-клиентов** — тот же паттерн `grep -cE "^I[1-5] = " || echo 0` → краш при `[[ "0\n0" -gt 0 ]]`. Пользователь не видел QR/конфиг.
- **HIGH: Nested `set -e` clobbering** — меню-функция делает `set +e`, вызывает подменю, которое в конце делает `set -e` → `set -e` оставался внутри родительского `while` цикла. Последующий `read -rp` с EOF/Ctrl-D убивал родительское меню. **Фикс:** `set +e` после каждого вложенного вызова (9 мест).
- **HIGH: `SERVER_REGION` хардкод "ru"** — `do_autoinstall` и `do_add_client_noninteractive` хардкодили `SERVER_REGION="ru"` + RU-пулы доменов. На world-сервере `--add-client` генерировал I1 TLS с ya.ru/vk.com (недоступны из EU) → пустой CPS → клиент без I1. **Фикс:** новая функция `_detect_server_region()` читает `# Region:` из `$SERVER_CONF`; дефолт — `"world"`.
- **HIGH: `SKIP_DEPS` мёртвый код** — `do_update_bot` ставил `SKIP_DEPS=1`, но `do_install_bot` никогда его не проверял → apt-get/pip переустанавливались на каждом обновлении. **Фикс:** обёртка в `if [[ "${SKIP_DEPS:-0}" != "1" ]]`.
- **HIGH: `TimeoutExpired` не ловится** — `run()` в боте использовал `subprocess.run(timeout=30)` без `except TimeoutExpired`. Зависший `awg show` крашил handler task. **Фикс:** `except subprocess.TimeoutExpired: return (-1, "", "timeout")`.
- **HIGH: Permission leak через `mv`** — `do_clean_clients` и `do_bulk_add_clients` rollback делали `mv "$tmp" "$SERVER_CONF"` → SERVER_CONF становился 644 (world-readable PrivateKey). **Фикс:** `chmod 600` после каждого `mv` (6 мест).
- **HIGH: Нет rollback на `awg set` failure** — `do_add_client` и `do_add_client_noninteractive` оставляли orphaned peer в SERVER_CONF при ошибке `awg set`. **Фикс:** удаление последних 6 строк из SERVER_CONF при failure.
- **HIGH: Race condition на SERVER_CONF** — `expire_set`/`expire_clear`/`note_set`/`note_clear` делали read-modify-write без блокировки, конкурируя с `add_client` и expire-timer. **Фикс:** `_server_conf_lock()` context manager (`fcntl.flock` на `/run/awg-bot.lock`) для всех 4 функций.
- **HIGH: Утечки ресурсов (socket/SSH)** — `socket.socket()` без `with`/`try-finally` (FD leak при `connect` failure), `ssh = get_ssh_client()` без `try-finally` (SSH transport leak при `execute_remote` failure). Poller каждые 15с → FD exhaustion за дни. **Фикс:** `contextlib.closing()` для socket, `try-finally: ssh.close()` для всех SSH-клиентов (12 мест).
- **HIGH: Single-threaded HTTPServer** — долгие SSH-операции (`force-refresh`, `speedtest`, `deploy-chain`) блокировали ВСЕ клиенты. **Фикс:** `ThreadingHTTPServer` вместо `HTTPServer`.
- **HIGH: `config_lock` только в `reload_servers`** — POST handlers делали `load_config()` → mutate → `save_config()` без блокировки. Concurrent POSTs → lost updates. **Фикс:** `with config_lock:` вокруг всех RMW (6 handlers).
- **MEDIUM: `do_autoinstall` хардкод `# Region: ru`** — в server config писалось `echo "# Region: ru"` вместо `${SERVER_REGION:-world}`.
- **MEDIUM: `do_self_update` без checksum** — скачивание без проверки SHA256 (только `bash -n` + размер >50KB). Компрометация GitHub → root RCE.
- **MEDIUM: `do_reset_server` hardcoded /24** — использовал `cut -d. -f1-3` + `/24` для очистки iptables. Для /23 или /22 — неполная очистка. **Фикс:** `ipaddress.ip_network(strict=False)` + /24 fallback.
- **MEDIUM: `is_handshake_recent` regex** — `"5min"` (без пробела) не матчило `(\d+)\s+minute` → возвращал True (неверно). **Фикс:** `\s*` вместо `\s+`.
- **MEDIUM: `grep|cut||echo` dead code** (18 мест) — `cut` всегда exit 0, `|| echo "default"` никогда не выполнялся → `mode=""` вместо `"all"` → cascade routing мог молча отключиться. **Фикс:** `${var:-default}` после pipeline.
- **MEDIUM: IP regex принимает `999.999.999.999/99`** — `do_gen` валидировал только формат, не диапазоны октетов/маски. **Фикс:** `_valid_ip_cidr()` с bounds check.
- **MEDIUM: `sed` regex injection** — `sed -i "/^${ip}$/d"` в `_xray_peer_del` — `.` в IP действует как wildcard. **Фикс:** `grep -vxF` (точное совпадение).
- **MEDIUM: Trap leak** — `scan_pool`/`do_check_domains` делали `trap - INT TERM` на early return, clobberя глобальный trap. **Фикс:** восстановление глобального trap.
- **MEDIUM: `_cb_restart` discard `awg-quick down` return code** — stderr/code не выводился → confusing error на `up`. **Фикс:** сохранение `down_rc`/`down_err`.
- **MEDIUM: `apt-get install 2>/dev/null`** — скрывал реальную ошибку от пользователя. **Фикс:** повторный вывод `2>&1 | tail -20 >&2` в handler ошибки.
- **MEDIUM: SSE speedtest hang** — `for line in p.stdout` блокировал навсегда при зависании subprocess. **Фикс:** `select()` с 300s deadline + `p.kill()`.
- **MEDIUM: `read_post_json` ValueError** — `int(Content-Length)` без try/except + нет лимита размера. **Фикс:** try/except + 1MB limit.
- **MEDIUM: Hardcoded IPs в restart actions** — `restart-xray`/`restart-proxy-monitor` хардкодили IP/username. **Фикс:** динамическое чтение из `nodes_config.json`.
- **MEDIUM: `load_config()` schema validation** — `config["nodes"]` мог быть `null`/`string` → `TypeError` в `poll_all_servers`. **Фикс:** `isinstance(config.get("nodes"), list)`.
- **MEDIUM: `collect_server_status()` KeyError** — `srv["name"]` без `.get()` → silent thread death при malformed config. **Фикс:** `srv.get("name", "unknown")`.
- **MEDIUM: HTML-escape в боте** — `c['name']` в HTML без `_html.escape()`. **Фикс:** экранирование во всех HTML-контекстах.
- **LOW: `eval "$route_cmd"`** — заменён на массив `"${route_args[@]}"`.
- **LOW: `. /etc/os-release`** — clobberил globals (`VERSION`, `ID`, `PRETTY_NAME`). **Фикс:** `grep` extraction вместо `source`.
- **LOW: `rand_range` Python interpolation** — `python3 -c "...randint($lo, $hi)"` → `sys.argv`.
- **LOW: `OLD_TOKEN`/`OLD_CHAT_ID` без `local`** — утечка в global scope.
- **LOW: `_xray_down` missing `local`** — `client_net`/`iface` в global scope.
- **LOW: `g('mtu', '1380')` empty value** — `sp.get('mtu', '1380')` возвращал `""` для `MTU = `. **Фикс:** `v if v else default`.
- **LOW: `s["pass"]` KeyError** в `run_speedtests_seq.py` — нет проверки для `auth: "password"`.
- **LOW: `SSH_KEY_PATH` hardcoded** в `dashboard_dev.py` — `C:\Users\dante\...`. **Фикс:** `$SSH_KEY_PATH` env var + `USERPROFILE`.
- **LOW: "11 servers" в логе** `run_speedtests_seq.py` — хардкод вместо `len(servers)`.
- **LOW: `proxy node services`** — добавление proxy-узла ставило только `["vpn-route-monitor"]`, теряя `mtproxymax`.
- **LOW: `single-hop chain`** — `range(0,0,-1)` = пусто → 0 туннелей, но цепочка "deployed". **Фикс:** валидация `len(hops) >= 2`.
- **LOW: `authorized_keys` escape** — `escaped_key` экранировал только `"`, не backticks/`$()`. **Фикс:** валидация формата + single-quote.
- **LOW: `p.wait(timeout=5)` в except** — мог выкинуть повторный `TimeoutExpired`. **Фикс:** `p.kill()` + `try/except`.

### Прочее

- Добавлены файлы `awg2.sh.sha256` и `awg-bot-install.sh.sha256` для проверки целостности при self-update.
- Добавлен `AGENTS.md` — база знаний для работы с репозиторием (структура, конвенции, найденные баги).
- `dashboard.py` (устаревший): применены те же фиксы (socket/ssh try-finally, bare except).

## [v6.9.3-lucx] — 2026-06-14

### Добавлено (LucX Edition)
- **Автоматическая установка (CLI-интерфейс):**
  - Флаг `-auto` / `--auto` (или `AUTOINSTALL=1`): Полностью неинтерактивная установка сервера с наиболее устойчивыми к блокировкам параметрами (профиль **Pro**, маскировка **TLS ClientHello** под случайный домен, MTU `1320`, DNS `1.1.1.1`).
  - Флаг `--add-client <имя>`: Мгновенное добавление нового пользователя из терминала без входа в интерактивное меню (с автоматическим подбором свободного IP-адреса и выводом QR-кода).
  - Флаг `--interactive`: Принудительный запуск интерактивного меню на чистом сервере (по умолчанию при отсутствии конфигурационного файла скрипт теперь автоматически запускает установку `--auto`).
- **Скрытие токена Telegram-бота:** Все запросы к API Telegram переведены на передачу токена через стандартный ввод (`stdin`) утилиты `curl` вместо аргументов командной строки (`argv`), чтобы токен не светился в логах процессов (`ps`).

### Синхронизация с Upstream (v6.9.2 - v6.9.3)
- **Обновление CPS-генератора пакетов мимикрии (Версия 3):**
  - Добавлена вставка GREASE-значений в шифры и расширения для точной имитации поведения браузера Google Chrome.
  - Реализовано полноценное шифрование пакетов **QUIC Initial** по стандарту RFC 9001 (с использованием Python-библиотеки `cryptography` и безопасным маскированным fallback в случае её отсутствия).
  - Оптимизирован размер пакетов мимикрии TLS (лёгкий случайный паддинг вместо фиксированного хвоста из нулей, что защищает от детектирования по размеру и сигнатуре).
  - Флаг `--only-i1` теперь корректно обрабатывается в любой позиции аргументов командной строки.
- **Интеграция вызовов меню:** Ко всем пунктам меню добавлена обработка ошибок (`|| true`), чтобы случайный сбой дочерней функции не крашил выполнение основного скрипта (работающего под `set -e`).
- **Улучшенная валидация ручного ввода IP:** При создании клиента вручную добавлена жесткая проверка на формат, попадание в диапазон подсети сервера, занятость адреса другими клиентами и предотвращение конфликта с адресом самого интерфейса сервера.
- **Уникальные имена по умолчанию:** Первому генерируемому клиенту при установке теперь присваивается случайное уникальное имя (например, `xkqve_73`) вместо стандартного `client1`.

### Другие изменения (LucX Edition)
- Полностью пересобрана история коммитов: ветка `main` переведена на чистую линейную структуру поверх официальных коммитов родительского репозитория.
