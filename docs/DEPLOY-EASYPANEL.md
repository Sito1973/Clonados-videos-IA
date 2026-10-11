# Despliegue en una VPS con EasyPanel

Esta guía instala Genjustsu en tu VPS con EasyPanel. La app queda protegida con contraseña y usa **kie.ai** para Seedance 2.5. Tus videos no se suben a hosts públicos: van al almacenamiento temporal de kie.ai, que los borra a las 24 horas.

## Antes de empezar

- Una VPS con EasyPanel funcionando. Se recomiendan al menos 2 vCPU, **4 GB de RAM** y 20 GB libres.
- Un subdominio, por ejemplo `video.tudominio.com`, con un registro DNS **A** que apunte a la IP de la VPS.
- Tu API key de kie.ai, con crédito.
- Opcional: un token de Replicate si quieres los modos con profundidad y SAM 3. Sin él solo funciona el modo rápido **Fast · Face mesh**, porque kie.ai no ofrece esos modelos.

> **Nunca pegues tus claves en un chat ni en GitHub.** Van solo en la pestaña *Environment* de EasyPanel.

## 1. Crear la app

1. En EasyPanel, abre o crea un proyecto y pulsa **+ Service → App**. Ponle un nombre, por ejemplo `genjustsu`.
2. En **Source**, elige **GitHub** con el propietario `Sito1973`, el repositorio `Clonados-videos-IA`, la rama `claude/easypanel-kie-deploy` (o `main` cuando la fusiones) y la ruta `/`.
   - Si EasyPanel te pide un token de GitHub, usa en su lugar la opción **Git** con la URL `https://github.com/Sito1973/Clonados-videos-IA.git` y la misma rama.
3. En **Build**, elige **Dockerfile** con el archivo `Dockerfile`.

## 2. Variables de entorno

En **Environment**, pega esto y cambia los tres valores marcados:

```env
SEEDANCE_PROVIDER=kie
AUDIO_SEPARATION=kie
KIE_API_KEY=pega-aqui-tu-clave-de-kie
APP_PASSWORD=una-contrasena-larga-y-unica
PUBLIC_BASE_URL=https://video.tudominio.com
MEDIA_HOST=kie
MEDIA_HOST_FALLBACKS=
```

Variables opcionales:

```env
# Activa los modos con profundidad + SAM 3 (Replicate cobra por uso)
REPLICATE_API_TOKEN=
# Clave para firmar sesiones y enlaces. Si la omites, se crea sola y se guarda en el volumen.
APP_SECRET=
# Horas de validez de los enlaces que recibe kie.ai (por defecto 168 = 7 días)
MEDIA_LINK_HOURS=168
```

- `PUBLIC_BASE_URL` debe ser exactamente tu dominio, con `https://` y sin barra al final.
- `MEDIA_HOST=kie` sube tus archivos al almacenamiento temporal de kie.ai, desde donde sus servidores los descargan sin problemas. kie.ai los borra a las 24 horas, así que **pide el 1080p dentro de ese plazo**. Con `MEDIA_HOST_FALLBACKS=` vacío, nada se sube a tmpfiles ni a catbox.
- Alternativa: `MEDIA_HOST=self` sirve los archivos desde tu propio dominio con enlaces secretos. Puede fallar si los servidores de ByteDance no llegan a tu VPS (error `File processing failed, please check the URL`).
- Si cambias `APP_PASSWORD`, todas las sesiones abiertas se cierran.

## 3. Volumen persistente

En **Mounts**, pulsa **Add Volume** con el nombre `runs` y la ruta de montaje `/app/runs`. Ahí se guardan tus trabajos, los resultados y la clave de firma. **Sin este volumen, todo se pierde en cada despliegue.**

## 4. Dominio

En **Domains**, añade `video.tudominio.com`, activa **HTTPS** y apunta al puerto **8770** del contenedor.

## 5. Desplegar

Pulsa **Deploy**. La primera construcción tarda varios minutos, porque instala Python, Rubber Band y MediaPipe y descarga el modelo facial. En los logs debe aparecer `Uvicorn running on http://0.0.0.0:8770`.

## 6. Comprobar que todo funciona

- `https://video.tudominio.com/health` devuelve `{"status":"ok",...}`.
- `https://video.tudominio.com` muestra la pantalla de inicio de sesión. Entra con tu `APP_PASSWORD`.
- En el paso **03** debe aparecer **Provider · kie.ai**. Si no configuraste Replicate, en el paso **01** solo estará habilitado **Fast · Face mesh**.
- Opcional: en la consola del contenedor (EasyPanel → *Console*) ejecuta `python doctor.py --mesh`. No gasta crédito.

## 7. Primera prueba (gasta crédito)

1. Sube un clip corto, de 5 a 10 segundos, con **una sola persona centrada** y voz clara. Elige **Fast · Face mesh** y pulsa **Prepare video**. Esto cobra una separación de voz en kie.ai.
2. Revisa el video preparado completo: la malla facial debe estar bien alineada en todos los fotogramas. Escucha también la voz. Después marca la casilla y pulsa **Approve input**.
3. Sube la imagen de tu personaje, revisa el prompt y pulsa **Create draft**. kie.ai genera un borrador a 480p.
4. Si te gusta, pulsa **Approve & generate 1080p**. kie.ai completa ese mismo borrador a 1080p, y se cobra aparte.

El video final siempre lleva el **audio original completo**. El audio generado se descarta.

## Qué cobra kie.ai

- **Separación de voz** (`ai-music-api/separate-vocals`): se cobra por solicitud.
- **Seedance 2.5**: se cobra por los segundos de video de referencia más los segundos generados. El borrador sale a 480p; el paso a 1080p es otra tarea con su propio cobro.
- Según kie.ai, las tareas fallidas no se cobran. Consulta los precios actuales en <https://kie.ai/pricing>.

## Caras reales

Seedance 2.5 tiene restricciones para videos e imágenes de referencia con caras de personas reales. Si kie.ai rechaza una tarea, verás su mensaje en la app y también quedará guardado en `provider-status.json` dentro del trabajo. Nada se reintenta solo. Usa personas que hayan dado su permiso, o personajes que no sean personas reales.

## Actualizar

Cuando haya cambios en la rama, pulsa **Deploy** de nuevo, o activa *Auto Deploy*. El volumen `runs` conserva tus trabajos. Si un trabajo estaba generándose durante el reinicio, la app retoma sola la descarga del resultado sin volver a enviarlo.

## Problemas frecuentes

| Síntoma | Causa probable |
|---|---|
| El login se repite una y otra vez | Entraste por `http://` en vez de `https://`, o `PUBLIC_BASE_URL` no coincide con el dominio. |
| `File processing failed, please check the URL` | El proveedor no pudo descargar tus archivos. Usa `MEDIA_HOST=kie`. |
| `All configured media hosts failed (self: ...)` | `PUBLIC_BASE_URL` es incorrecto, el DNS aún no se propagó o HTTPS no está activo. Comprueba que `PUBLIC_BASE_URL/health` abre en tu navegador. |
| `Depth modes need REPLICATE_API_TOKEN` | Usa **Fast · Face mesh** o añade un token de Replicate. |
| `kie.ai rejected the task (402)` | No tienes crédito suficiente en kie.ai. |
| `kie.ai rejected the task (401)` | La API key es incorrecta. |
| `kie.ai task failed: ...` | kie.ai rechazó la generación. El motivo aparece en el mensaje (por ejemplo, la restricción de caras). |
| `Use a clip of at least 4 seconds` o `needs reference videos of 4–30 seconds` | kie.ai edita videos de 4 a 30 segundos. Usa un clip dentro de ese rango. |
| `kie.ai accepts at most 30 s of reference video` | El video del personaje más el clip suman más de 30 s. Usa un video del personaje más corto. |
| `Face tracking gaps` | La cara no se detecta en algunos fotogramas. Usa un clip con la cara visible y centrada todo el tiempo. |
| El contenedor se reinicia mientras procesa | Le falta RAM a la VPS. Sube a 4 GB o más. |

Para el detalle técnico de las llamadas, consulta [KIE-API.md](KIE-API.md).
