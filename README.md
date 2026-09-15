# Paneles de servidores de Roblox para Discord

Bot en **Python 3.12+** preparado para Pterodactyl. Un panel independiente por juego y servidor de Discord, configurable con comandos o el botón **Configurar**. Prioriza conteos recientes de **0–1 jugadores**. No necesita integrarse con el bot que te avisa de los items.

## Uso en Discord

```text
/panel crear juego:2753915549 canal:#blox-fruits
/panel crear juego:https://www.roblox.com/games/920587237/Adopt-Me canal:#adopt-me
/panel listar
/panel configurar panel:ID max_jugadores:1 intervalo:10 vigencia:15 paginas:2
/panel perfil panel:ID modo:equilibrado
/panel pausa panel:ID pausado:true
/panel pausa panel:ID pausado:false
/panel reparar panel:ID canal:#nuevo-canal
/panel eliminar panel:ID
/estado
```

Cada panel tiene nombre, PlaceId, canal, filtro, intervalo, vigencia y presupuesto de páginas propios. **Configurar** abre un formulario con los valores existentes. El comando `/panel configurar` establece todos los parámetros mostrados (los omitidos toman los valores por defecto del comando). Usa `/panel listar` para copiar su ID. Para cambiar de juego crea otro panel; no se mezcla el historial entre Places.

Los administradores necesitan **Gestionar servidor**. Los usuarios que ven el canal pueden actualizar o seleccionar candidatos. No hace falta el intent privilegiado Message Content.

### Flujo de entrada

1. Recibes la notificación del otro bot.
2. Abres el canal del juego.
3. Cada resultado incluye **Entrar a esta instancia** y un botón directo con su ID abreviado. Para comprobarlo antes, pulsa **Buscar mejor ahora** o selecciona una instancia concreta. Los enlaces directos no revalidan al pulsarlos; los pasos siguientes corresponden a la comprobación.
4. Si el último dato tiene más de 5 segundos, el bot intenta reobservar esa misma instancia durante un máximo de 12 segundos.
5. Solo prepara el enlace si el dato tiene como máximo 5 segundos y sigue cumpliendo el filtro actual. Nunca sustituye silenciosamente el JobId elegido manualmente. **Buscar mejor ahora** sí permite elegir explícitamente una alternativa nueva. La entrega de Discord también puede tardar: comprueba siempre la fecha mostrada.
6. La respuesta es privada para ti. Se intenta retirar su enlace al cumplirse 10 segundos desde la observación. Quedan botones durante cinco minutos para reportar cuántos jugadores adicionales había o si no pudiste entrar. Si el bot se desconecta, Discord puede conservar el mensaje: un enlace visible no garantiza vigencia.

**Roblox controla la ocupación y el acceso.** Incluso un dato recién consultado puede estar cacheado por Roblox o cambiar antes de entrar. Los cero jugadores se muestran como observaciones inciertas, no como plazas garantizadas.

## Instalación en Pterodactyl

Consulta la [guía paso a paso](docs/PTERODACTYL.md). Resumen:

1. Egg **Python generic**, imagen **Python 3.12** o **3.13**.
2. Subir los archivos del proyecto, conservando la carpeta `serverbot`.
3. Archivo de arranque: `main.py`. Dependencias: `requirements.txt`.
4. Configurar `DISCORD_TOKEN` y, opcionalmente, `DISCORD_GUILD_ID` en el entorno o `.env`.
5. Conservar `data/` entre reinicios.
6. Arrancar. El log `BOT_READY` confirma conexión a Discord.

No se requiere un servidor web, dominio, puerto público ni un contenedor Docker anidado dentro de Pterodactyl.

## Instalación local

```sh
python -m venv .venv
# Windows: .venv\Scripts\activate
# Linux: source .venv/bin/activate
python -m pip install -r requirements.txt
# Copia .env.example a .env y configura el token de tu aplicación.
python main.py
```

Para Docker fuera de Pterodactyl: `docker compose up -d --build` después de crear `.env`.

### Diagnóstico sin token de Discord

```sh
python main.py --diagnose 2753915549
```

Hace una sola consulta, sin cookies, y devuelve `ROBLOX_OK` o una causa explícita. Código de salida 0: consulta compatible; 2: proveedor no disponible. No recorre toda la experiencia. El acceso puede variar según el host y cambios de Roblox.

## Configuración global

| Variable | Valor por defecto | Uso |
|---|---|---|
| `DISCORD_TOKEN` | vacío | Token del bot; obligatorio para conectarlo |
| `DISCORD_GUILD_ID` | vacío | ID de Discord de pruebas; registro de comandos en ese servidor |
| `DATABASE_PATH` | `data/bot.sqlite3` | SQLite persistente |
| `ROBLOX_REQUESTS_PER_MINUTE` | `30` | Presupuesto global propio, no cuota oficial |
| `MAX_ACTIVE_PLACES` | `5` | Juegos/Places distintos simultáneos |
| `MAX_PANELS_PER_GUILD` | `10` | Paneles por servidor de Discord |
| `PANEL_EDIT_SECONDS` | `5` | Intervalo mínimo entre ediciones de un panel |
| `JOIN_MODE` | `legacy` | `legacy` o `game`; explicado abajo |
| `LOG_LEVEL` | `INFO` | Nivel de logs |

Configurar 5 segundos en varios juegos **no garantiza** una consulta cada 5 segundos: hay reparto del presupuesto, paginación y esperas por errores. A 30 solicitudes/minuto se espacia cada petición al menos 2 segundos; cinco juegos que necesiten dos páginas cada diez segundos superarían el presupuesto. El bot no aumenta automáticamente la cuota para compensarlo. Reduce juegos activos o páginas si los datos caducan con frecuencia.

## Entrada: limitación actual importante

`JOIN_MODE=legacy` genera el enlace documentado históricamente `https://www.roblox.com/games/start?placeId=...&gameInstanceId=...`, con un botón **Intentar entrar · experimental**. Roblox marca ese procedimiento como obsoleto. Se incluye como intento de compatibilidad; no se ha comprobado aquí la entrada efectiva en un cliente Roblox. Probar en tus dispositivos antes de depender de él durante un evento.

`JOIN_MODE=game` ofrece solamente **Abrir juego**, sin prometer selección de instancia. También se muestra PlaceId y JobId. Los share links actuales no se pueden asumir como sustituto para cualquier juego ajeno.

## Qué hace el motor

- Consulta páginas ascendentes con `excludeFullGames=true`, hasta 100 entradas por página.
- Cada Place tiene un trabajo independiente; paneles del mismo Place en distintos Discord comparten consultas.
- Combina exploración progresiva y reobservación dirigida mediante pistas de cursores de corta duración. Mantiene hasta 50 referencias prioritarias por juego.
- Detiene el recorrido según la evidencia obtenida, al acabar páginas, ante cursores repetidos o tras 20 segundos. Cinco resultados provisionales no detienen la exploración del perfil equilibrado.
- Reobserva candidatos únicamente cuando vuelven a aparecer en una respuesta. Una ausencia no significa cero jugadores ni cierre.
- Excluye cada dato al superar la vigencia configurada. Al pulsar comprueba además el límite de 5 segundos.
- Ranking por frescura, historial, cobertura, tendencia de crecimiento y picos de llenado. Un cero aislado puede quedar por debajo de un servidor con una persona reobservado. Calidad 0–100 es una puntuación heurística, no una probabilidad.
- Perfiles independientes por panel: equilibrado, precisión, evento y rápido. Precisión exige reobservación y vigencia máxima de diez segundos; evento exige evidencia posterior a activarlo y máximo ocho segundos.
- Persistencia de paneles e historial. Tras reiniciar, ningún dato guardado se ofrece como recomendación actual hasta reobservarlo.
- El filtro de 0–1 nunca se amplía automáticamente. Si no hay resultados, el panel lo dice.
- Cola acotada por juegos activos, dos peticiones HTTP simultáneas como máximo, sin ráfagas, backoff y `Retry-After` global.
- Ante 429 reduce automáticamente el ritmo; lo recupera gradualmente sin superar el presupuesto configurado. La presión de llenado y el perfil evento ajustan el intervalo solicitado dentro de ese presupuesto.
- Medición prospectiva del TOP a 10–30 segundos: aciertos, fallos y desconocidos separados. Botón **Calidad** con cobertura y reportes de entrada.
- `401/403`: espera y explica la restricción, sin intentar cookies ni evasión.
- Botones persistentes, comprobación de permisos al actuar y recuperación manual de mensajes borrados.

## Historial y copias de seguridad

SQLite guarda detalle de cambios y al menos una muestra por minuto durante 48 horas. Además conserva hasta 120 muestras recientes por instancia en un resumen móvil de treinta minutos. Elimina referencias no vistas y mediciones con más de siete días. La limpieza de observaciones y referencias se hace en lotes de 5000 filas por minuto; bajo grandes acumulaciones puede tardar varios ciclos. La base reutiliza páginas libres, no se reduce en disco automáticamente. La migración desde v1 conserva paneles e historial.

Utiliza las copias de Pterodactyl con el bot detenido, o la API `sqlite3.Connection.backup()` para una copia consistente en caliente. No copies únicamente el archivo principal mientras haya escrituras WAL. Nunca incluyas `.env` en un ZIP público.

## Pruebas

```sh
python -m pip install -r requirements-dev.txt
python -m pytest -q
```

Incluyen caducidad individual, ocupación estricta, identidad del servidor seleccionado, cambios de configuración durante el flujo, persistencia sin falsa continuidad, cursores repetidos, límites globales, HTTP simulado local, aislamiento entre Discord y serialización de comandos. No necesitan token ni hacen consultas a Roblox.

## Configuración inicial recomendada

Para Steal An Egg empieza con `max_jugadores:1 intervalo:10 vigencia:15 paginas:2` y perfil **equilibrado**. Mantén `JOIN_MODE=legacy` para enlaces por servidor. Si quieres priorizar candidatos confirmados aunque el panel quede vacío, elige **precisión**. El perfil **evento** reinicia la evidencia y reduce la vigencia; se activa manualmente tras el aviso externo.

El presupuesto global por defecto de 30/min no garantiza acceso: en una prueba real este juego devolvió 429 tras dos rondas. Evita aumentar peticiones ante un límite; el bot reduce el ritmo automáticamente. Varios juegos pueden requerir intervalos mayores.

## Investigación y objetivo del 70 %

Se inspeccionaron RoValra, BTRoblox y robloxserverfinder. Consulta la [investigación técnica, algoritmos, limitaciones y protocolo de medición](docs/INVESTIGACION.md). Incluye fuentes fijadas a commits y el resultado real con Steal An Egg.

**No se ha demostrado todavía un 70 % de entradas con 0–1 jugadores.** El bot mide reobservaciones de la API y reportes voluntarios por separado. No convierte ausencias en éxitos ni usa su puntuación como porcentaje. No detecta automáticamente eventos del otro bot ni controla el matchmaking de Roblox.

Puedes capturar datos públicos y comparar los perfiles con `tools/audit.py`; las instrucciones y los límites de esa evaluación están en la investigación.

## Referencias de compatibilidad

- [Restricciones anunciadas sobre la lista de servidores](https://devforum.roblox.com/t/test-updates-to-server-list-page/3966648)
- [Deep links heredados, marcados como obsoletos](https://create.roblox.com/docs/production/promotion/deeplinks)
- [Share links](https://create.roblox.com/docs/production/promotion/share-links)
- [Rate limits de Roblox](https://create.roblox.com/docs/cloud/reference/rate-limits)
- [discord.py](https://discordpy.readthedocs.io/en/stable/)
- [Python generic para Pterodactyl](https://eggs.pterodactyl.io/egg/generic-python-generic/)
