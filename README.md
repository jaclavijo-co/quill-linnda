# Quill Linnda

Graba tus reuniones desde el Mac (Meet, Zoom, Teams, WhatsApp…), las transcribe y arma la nota por cliente en tu Google Drive. Sin bots en la llamada y sin suscripción.

## Instalar (Mac con macOS 15 o más reciente)

Abre la app **Terminal**, pega esta línea y dale Enter:

```bash
curl -fsSL https://raw.githubusercontent.com/jaclavijo-co/quill-linnda/main/install.sh | bash
```

Te pide tu nombre, tu correo, la llave de Gemini (pídesela a José) y la dirección iCal de tu calendario. Toma unos 3 minutos. Guía paso a paso: [docs/GUIA-EQUIPO.md](docs/GUIA-EQUIPO.md).

## Cómo se usa

- **Graba sola** cuando estás en una reunión de tu calendario (suena un aviso y la pluma se pone roja con "REC"). Si la llamada no está en el calendario, la pluma se pone naranja con "¿grabar?": clic → *Empezar a grabar*.
- **Para sola** un minuto después de colgar. La nota aparece en *Mi unidad/Notas-Llamadas/<Cliente>/* en 2 a 4 minutos.
- **Reuniones presenciales:** graba con el teléfono y compártelo a la carpeta *Linnda Grabaciones* de tu Drive.
- **Buscar:** `notetaker buscar <texto>` · `notetaker lista` · `notetaker estado` en la Terminal.

## Qué trae cada nota

Resumen, participantes, detalles por tema, acuerdos, próximos pasos con responsable, preguntas abiertas, cifras clave y la transcripción completa con el nombre de quién habló.

## Privacidad

- El audio se envía a la API de Gemini (Google) para transcribirlo y resumirlo, igual que Gemini en Meet. Avísale a los demás que estás tomando nota con IA.
- Tu llave y tu calendario quedan solo en tu Mac (`~/.notetaker/.env`, permisos 600). Este repo no tiene llaves, calendarios ni datos de clientes.
- La pluma se actualiza sola cada mañana desde este repo.

## Actualizar o desinstalar

```bash
curl -fsSL https://raw.githubusercontent.com/jaclavijo-co/quill-linnda/main/install.sh | bash -s -- --update
curl -fsSL https://raw.githubusercontent.com/jaclavijo-co/quill-linnda/main/install.sh | bash -s -- --uninstall
```

## Para quien desarrolla

- `app/`: proceso de notas en Python (transcripción por trozos, voces con nombre, calendario, contexto del cliente, archivo por cliente).
- `quill/`: la pluma en Swift (graba micrófono + audio del sistema con Core Audio, detecta llamadas, arranque automático por calendario).
- Publicar una versión: sube `VERSION`, crea la etiqueta `vX.Y.Z` y GitHub Actions compila la pluma (Apple Silicon + Intel) y la publica como release.
- Windows y Linux: fase 2 (pendiente).
