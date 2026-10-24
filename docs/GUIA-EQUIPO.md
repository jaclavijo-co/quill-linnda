# Quill en tu Mac: guía de 5 minutos

**Antes de empezar** necesitas: un Mac con macOS 15 o más reciente, Google Drive para escritorio con tu cuenta de Linnda (google.com/drive/download) y la llave de Gemini que te pasa José por mensaje privado.

## 1. Instalar

1. Abre **Terminal** (Cmd + Espacio, escribe "Terminal").
2. Pega esta línea y dale Enter:

   ```bash
   curl -fsSL https://raw.githubusercontent.com/jaclavijo-co/quill-linnda/main/install.sh | bash
   ```

3. Responde lo que te pregunta: nombre, cómo te dicen en las reuniones, correo, rol y la llave de Gemini (al pegarla no se ve: es normal).
4. Cuando pida la **dirección iCal**, se abre Google Calendar: ⚙️ Configuración → en la izquierda, tu calendario → *Integrar calendario* → *Dirección secreta en formato iCal* → copiar y pegar en la Terminal. No la compartas con nadie.

## 2. Dar los permisos (una sola vez)

1. Busca la **pluma** en la barra de menú, arriba a la derecha (si tienes muchos íconos puede quedar escondida detrás de la cámara; cierra alguno).
2. Clic → **Empezar a grabar**. macOS pide dos permisos para *quill*: **Micrófono** y **Grabación de pantalla y audio del sistema**. Acepta los dos.
3. Si no aparecen: Ajustes del Sistema → Privacidad y seguridad → activa *quill* en ambos.
4. Habla 30 segundos → **Detener grabación**. En 1 a 2 minutos aparece tu primera nota en *Mi unidad/Notas-Llamadas*.

## 3. Día a día

- En reuniones de tu calendario **graba sola** (suena un aviso y la pluma se pone roja con "REC") y **para sola** al colgar.
- Llamada que no está en el calendario: la pluma se pone naranja con **"¿grabar?"** → clic → *Empezar a grabar*.
- Reunión presencial: graba con el teléfono → Compartir → Drive → **Linnda Grabaciones**.
- Avísale a los demás que estás tomando nota con IA.

## Si algo falla

- `notetaker estado` en la Terminal revisa la instalación.
- Volver a correr la línea de instalación arregla casi todo (no borra tus notas).
- Escríbele a José o a Rafa con el resultado de `notetaker estado`.
