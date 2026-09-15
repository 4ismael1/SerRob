# Validación del MVP — 15 de septiembre de 2026

## Comprobado

- **31 pruebas automatizadas aprobadas en Python 3.12.10, Windows x64.** Se utilizó una distribución embebida oficial aislada en `.validation-runtime/`, que no se incluye en el paquete de despliegue.
- Las 30 pruebas existentes antes de añadir la prueba del planificador también pasaron en Python 3.14.6. No se atribuye a 3.14 la ejecución de la última prueba añadida.
- Sintaxis del código de aplicación comprobada para Python 3.12.
- Importación de los módulos Discord y serialización del esquema de comandos sin credenciales.
- Dependencias directas fijadas: discord.py 2.7.1, aiohttp 3.14.3, python-dotenv 1.2.2.
- Resolución de dependencias verificada para CPython 3.12 / Linux manylinux2014 x86_64, incluyendo disponibilidad de wheels; esto no equivale a ejecutar el bot en Linux.
- Prueba HTTP real, sin cookies, con Place `2753915549`: primera página con 100 registros válidos y conteos de una persona.
- Dos recorridos reales del motor separados por seis segundos para ese Place: cada uno se detuvo tras una página y produjo cinco candidatos elegibles, sin errores. Se hicieron dos solicitudes. No reaparecieron los mismos IDs en esa pequeña prueba: esto refuerza la necesidad de caducidad individual y del botón **Buscar mejor ahora**. No permite inferir que los servidores anteriores cerraran.
- Diagnóstico real desde Python 3.12 con un segundo Place, `920587237`: 100 registros válidos y conteos de una persona.

Las observaciones anteriores describen respuestas en el momento de la prueba, no disponibilidad futura. No se guardaron cookies, tokens de usuario ni listas de jugadores.

## Cobertura automatizada

- PlaceId y enlaces compatibles; rechazo de dominios arbitrarios, credenciales en URL y formatos ambiguos.
- Validación del esquema, IDs, ocupaciones y cursores.
- Frescura individual; exclusión de observaciones antiguas o futuras.
- Filtro estricto y selección por JobId, no por una posición cambiante del ranking.
- Comprobación de pausa/filtro actualizado al preparar entrada.
- Búsqueda explícita de la mejor alternativa reciente.
- Historial persistente sin extender estabilidad a través de huecos.
- Inicio en frío sin tratar la base de datos como evidencia actual.
- Detención temprana y cursores repetidos.
- Presupuesto global, `Retry-After` numérico y fecha HTTP.
- HTTP local simulado: 400, 401, 403, 404, 429, 503, redirecciones y edad de caché.
- Controles persistentes, configuración versionada y aislamiento entre servidores de Discord.
- Compartición de consultas solo entre Places iguales y pausa independiente de otros juegos.
- Limpieza del historial sin borrar paneles.

## Pendiente en el alojamiento del usuario

- Conexión real con el token de su aplicación de Discord, permisos y registro de comandos.
- Ejecución real dentro de Pterodactyl/Linux; las pruebas ejecutadas aquí son de Windows.
- Prueba prolongada con el número de juegos elegido y sus límites de red.
- Entrada efectiva desde Discord a un JobId concreto en sus dispositivos. El mecanismo heredado es experimental y Roblox lo documenta como obsoleto.
- Prueba de recuperación con mensajes reales borrados y reinicio de Pterodactyl.

Advertencia de dependencias durante pytest en Python 3.12: discord.py importa `audioop`, que Python marca como obsoleto. No es un fallo del test ni una función de voz usada por este bot.
