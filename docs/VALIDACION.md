# Validación — 15 de septiembre de 2026

## Ampliación avanzada

### Corrección de seguimiento sin resultados

- **51 pruebas aprobadas en Python 3.12.10 / Windows.** Una simulación integral con cien instancias por página, una página por ciclo e intervalo de quince segundos obtiene cinco candidatos estables a los noventa segundos de reloj simulado, con siete solicitudes. No es una medición real de Roblox.
- Se reproduce el empate de candidatos que dejaba las páginas posteriores fuera de las cincuenta referencias prioritarias. El seguimiento utiliza ahora un grupo explícito de hasta diez JobIds de una página.
- Otra prueba devuelve instancias diferentes en cada respuesta: el grupo se libera tras dos revisiones sin evidencia nueva y continúa el recorrido, sin recomendar resultados no confirmados.
- La vigencia de profundo sigue limitada a diez segundos: un intervalo de quince puede dejar huecos entre publicaciones. No se ha ampliado para disimular falta de datos ni se promete un plazo real de aparición.

### Corrección tras errores de entrada reportados

- Ampliación posterior: **49 pruebas** aprobadas en Python 3.12.10 / Windows. Incluyen el perfil profundo, el avance con una página por ciclo y la conservación del cursor de descubrimiento durante seguimiento. Las 47 indicadas a continuación corresponden al paso previo de corrección.

- 47 pruebas aprobadas en Python 3.12.10 / Windows. Nuevas regresiones: intervalo respetado ante evento/presión/actualización, bloqueo de entrega durante pausa del proveedor y ocupación que cambia mientras se prepara la respuesta privada.
- La entrega consulta la última observación en memoria antes y después de operaciones de persistencia. No sigue usando el conteo capturado antes de esas esperas.
- El panel explica si está vacío por error del proveedor, caducidad/filtro o falta de confirmación.
- El mensaje del usuario «esta experiencia terminó o el servidor falló por un error inesperado» no identifica por sí solo la causa. Estos cambios no prueban que el error del cliente Roblox esté resuelto. El enlace directo continúa sin revalidación al pulsar y su compatibilidad debe comprobarse en el dispositivo del usuario.

- 44 pruebas automatizadas aprobadas en Python 3.12.10 / Windows, incluyendo los casos anteriores.
- Casos añadidos: ranking con historial frente a cero aislado, abstención por huecos o crecimiento, reinicio de evidencia en eventos, vigencia sin prolongación por continuidad del TOP, migración aditiva de SQLite, medición prospectiva con desconocidos, reportes ligados a usuario/Discord, recuperación gradual de cuota, exploración y caducidad de pistas de cursores, cambio a precisión durante selección, coherencia entre botones y resultados y replay causal con llenado/ausencias.
- Prueba pública con Steal An Egg: primera ronda 200 instancias, segunda 15 con dos IDs repetidos, tercera detenida ante 429. Solo dos instantáneas: insuficientes para medir una tasa de éxito o mejora.
- Replay de esas instantáneas: cero resultados resueltos del TOP en la ventana posterior; rápido/equilibrado diez pendientes y precisión dos. No se interpretan como éxitos.
- No se ha probado una entrada real en el cliente Roblox ni el despliegue en el Pterodactyl del usuario.

Los 31 tests originales también pasaron en CI Linux Python 3.12 y 3.13 en el commit inicial. Las ejecuciones CI de la revisión avanzada se pueden comprobar en Actions; no deben confundirse con una prueba de alojamiento o de Discord real.

La [investigación](INVESTIGACION.md) detalla fuentes, método y limitaciones. Los apartados siguientes conservan la evidencia de la versión inicial.

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
