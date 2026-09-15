# Despliegue en Pterodactyl

## 1. Crear la aplicación de Discord

En el [Discord Developer Portal](https://discord.com/developers/applications):

1. Crea una aplicación y su bot.
2. Copia su token de forma privada a `DISCORD_TOKEN`. No lo pegues en un canal ni lo subas al repositorio.
3. Invítalo usando los scopes `bot` y `applications.commands`.
4. Permisos del bot: **Ver canales**, **Enviar mensajes**, **Insertar enlaces** y **Leer historial de mensajes**. No necesita Administrador.
5. No actives Message Content, Server Members ni Presence Intent.

Los administradores de paneles necesitan **Gestionar servidor**. Los demás usuarios pueden seleccionar y refrescar los paneles visibles para ellos.

## 2. Preparar el servidor de Pterodactyl

Usa el egg [Python generic](https://eggs.pterodactyl.io/egg/generic-python-generic/) con imagen:

```text
ghcr.io/ptero-eggs/yolks:python_3.12
```

Python 3.13 también es compatible por diseño; las versiones exactas realmente probadas se indican en el informe de validación. Una asignación inicial razonable para probar pocos juegos es 512 MB de RAM y 1 GB de disco, pero no es una medición ni un requisito garantizado.

Sube el contenido del paquete a `/home/container`:

```text
/home/container/main.py
/home/container/requirements.txt
/home/container/serverbot/...
/home/container/.env
```

El archivo `.env` no viene con credenciales: copia `.env.example`, renómbralo y rellénalo. Las variables reales de entorno tienen prioridad sobre `.env`.

Variables habituales del egg:

```text
PY_FILE=main.py
REQUIREMENTS_FILE=requirements.txt
USER_UPLOAD=1
AUTO_UPDATE=0
```

Comprueba los nombres en tu versión del egg. Su arranque debe instalar `requirements.txt` y ejecutar el archivo seleccionado. Si tienes un arranque Python propio, el comando final es:

```sh
python -u main.py
```

En la configuración de detección de arranque del egg, el administrador puede establecer:

```json
{"done": "BOT_READY"}
```

Esto evita que el panel quede visualmente como «iniciando» después de conectar. No se abre un puerto de aplicación; una asignación obligatoria del egg puede quedar sin uso.

## 3. Configurar y arrancar

Mínimo en `.env`:

```dotenv
DISCORD_TOKEN=tu_token_privado
DISCORD_GUILD_ID=ID_DE_TU_DISCORD
DATABASE_PATH=data/bot.sqlite3
ROBLOX_REQUESTS_PER_MINUTE=30
MAX_ACTIVE_PLACES=5
JOIN_MODE=legacy
```

`DISCORD_GUILD_ID` es el servidor de Discord, no un juego de Roblox. Con él los comandos se registran localmente en ese Discord. Déjalo vacío para registro global, que puede tardar en propagarse. Si cambias del modo local al global, revisa que no queden comandos de pruebas duplicados.

Arranca y espera `BOT_READY`. Después:

```text
/panel crear juego:ID_O_URL canal:#servidores-del-juego
```

Crea otro panel para cada juego adicional. Abre **Configurar** para ajustar su intervalo, vigencia, páginas o nombre. No se necesita una modificación del código.

## 4. Comprobar el proveedor desde el host

Antes de usarlo durante un evento, ejecuta en un terminal autorizado del contenedor:

```sh
python main.py --diagnose 2753915549
```

Si tu panel no ofrece una terminal de shell, pide al administrador ejecutar el diagnóstico o añadir temporalmente esos argumentos al comando de arranque. La consola estándar del proceso Python no ejecuta comandos de shell.

Interpretación:

- `ROBLOX_OK`: una página respondió con un esquema compatible. No garantiza todas las páginas ni entrada efectiva.
- `restricted`: Roblox exige acceso que este bot no tiene; no hay bypass integrado.
- `rate_limit`: respetar la espera y bajar presión.
- `network`: revisar DNS, salida HTTPS, hora del sistema y certificados del contenedor. No desactivar TLS.
- `schema`: posible cambio del proveedor; conservar el log sin secretos para revisarlo.

## 5. Prueba manual obligatoria de operación

1. Crear dos paneles para juegos diferentes; cambiar el filtro de uno y verificar que el otro no cambia.
2. Comprobar el sello temporal y que desaparecen candidatos caducados.
3. Seleccionar un servidor: verificar en Roblox que el enlace experimental llega al JobId esperado. El bot no puede certificarlo por sí solo.
4. Reiniciar Pterodactyl; verificar que sobreviven configuración y botones.
5. Pausar un panel y reanudarlo.
6. Borrar un mensaje de panel y recuperarlo con `/panel reparar`.

Si el enlace heredado no funciona en tus dispositivos, configura `JOIN_MODE=game`: el botón abrirá únicamente la página del juego, con esa limitación explícita.

## 6. Persistencia y solución de problemas

- No borres `data/` al actualizar código.
- Haz copias de seguridad antes de reinstalar el servidor o cambiar almacenamiento.
- Un cambio de intervalo no garantiza vencer los límites de Roblox. Menos juegos y menos páginas ayudan a mantener datos recientes.
- Si el bot ve cero resultados pero no errores, puede que ningún servidor visible cumpla el filtro.
- Si faltan comandos, revisa la invitación, scopes y `DISCORD_GUILD_ID`.
- Si un panel queda roto, restablece permisos y usa `/panel reparar`; no se recrea automáticamente en bucle.

No se ha conectado este proyecto a tu cuenta de Pterodactyl ni a Discord durante su desarrollo: necesitas configurar tu token y subir el paquete.
# Actualizar a la versión avanzada

Detén el bot y realiza una copia de seguridad consistente. Si tu instalación usa Git, actualiza desde `main` de SerRob mediante la opción de actualización de tu egg o `git pull --ff-only`. Si subiste archivos, reemplaza el código con el paquete nuevo conservando **`.env` y `data/`**. Reinstala `requirements.txt` si el egg no lo hace y arranca de nuevo. No subas `.research/` ni `.validation-runtime/`.

La migración SQLite de v1 a v2 es automática y conserva los paneles. El arranque vuelve a registrar comandos; `/panel perfil` añade selección de algoritmo por juego. Los comandos globales pueden tardar en aparecer en Discord. No borres la base para actualizar.

Para la estrategia de seguimiento estable utiliza `/panel perfil panel:ID modo:profundo` después de actualizar. Mantén inicialmente tus intervalos actuales para comparar sin aumentar tráfico. La estrategia y sus límites están en [ESTRATEGIA.md](ESTRATEGIA.md). El perfil se guarda en el panel existente y no requiere recrearlo.

Configuración inicial: máximo 1 jugador, intervalo 10 s, vigencia 15 s, dos páginas y perfil equilibrado. `JOIN_MODE=legacy` muestra enlaces por instancia. Elige precisión si aceptas menos resultados a cambio de exigir confirmación. El host comparte límites de red: no ejecutes una captura de auditoría junto al bot sobre la misma IP. Consulta [la investigación y medición](INVESTIGACION.md).
