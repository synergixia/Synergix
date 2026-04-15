import anthropic
import os
import json

# Inicializa el cliente con tu API KEY
client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))

# Definimos las "Manos" y "Ojos" de Claude
tools = [
    {
        "name": "leer_archivo",
        "description": "Lee el contenido de un archivo específico de tu repositorio para entenderlo.",
        "input_schema": {
            "type": "object",
            "properties": {"filepath": {"type": "string", "description": "Ruta del archivo (ej. aisynergix/bot.py)"}},
            "required": ["filepath"]
        }
    },
    {
        "name": "escribir_archivo",
        "description": "Sobreescribe o crea un archivo con nuevo código. Úsalo para aplicar los cambios solicitados por el usuario.",
        "input_schema": {
            "type": "object",
            "properties": {
                "filepath": {"type": "string", "description": "Ruta del archivo a guardar"},
                "content": {"type": "string", "description": "El código COMPLETO actualizado que se guardará"}
            },
            "required": ["filepath", "content"]
        }
    },
    {
        "name": "explorar_carpeta",
        "description": "Lista los archivos en un directorio para entender la estructura del proyecto.",
        "input_schema": {
            "type": "object",
            "properties": {"directory": {"type": "string", "description": "Ruta de la carpeta (ej. . o aisynergix/)"}},
            "required": ["directory"]
        }
    }
]

# El "Alma" del Agente
system_prompt = """Eres el Desarrollador Principal de IA de Synergix. 
El usuario chateará contigo. Tu entorno de trabajo es Termux en Android (Ubuntu).
Arquitectura del proyecto: Stateless, los datos viven en BNB Greenfield.

REGLAS DE ORO:
1. Puedes chatear libremente para aclarar dudas.
2. Si el usuario te pide implementar código o editar algo, NO le des solo el código en el chat. UTILIZA tu herramienta 'escribir_archivo' para inyectar el código directamente en el repositorio.
3. Si no conoces un archivo, usa 'leer_archivo' o 'explorar_carpeta' antes de editar.
4. Cuando uses 'escribir_archivo', manda siempre el código COMPLETO del archivo, no solo fragmentos."""

messages = []

print("==================================================")
print(" 🧠 SYNERGIX DEV AGENT INICIADO (Chat Libre) ")
print(" Escribe 'salir' para terminar la sesión. ")
print("==================================================\n")

while True:
    try:
        user_input = input("\n👤 Tú: ")
        if user_input.lower() in ['salir', 'exit', 'quit']:
            print("Guardando sesión y saliendo...")
            break
            
        messages.append({"role": "user", "content": user_input})
        
        while True:
            # Enviamos el chat y las herramientas a Claude
            response = client.messages.create(
                model="claude-sonnet-4-6",
                max_tokens=4096,
                system=system_prompt,
                tools=tools,
                messages=messages
            )
            
            # Mostramos lo que Claude responde en texto
            for block in response.content:
                if block.type == 'text':
                    print(f"\n🤖 Claude: {block.text}")
            
            messages.append({"role": "assistant", "content": response.content})
            
            # Detectamos si Claude decidió usar una herramienta (leer o escribir archivos)
            tool_calls = [b for b in response.content if b.type == 'tool_use']
            if not tool_calls:
                break # Si no hay herramientas que usar, esperamos tu próximo mensaje
                
            tool_results = []
            for tool in tool_calls:
                # 🛠️ Claude quiere LEER un archivo
                if tool.name == 'leer_archivo':
                    try:
                        with open(tool.input['filepath'], 'r') as f:
                            content = f.read()
                        tool_results.append({"type": "tool_result", "tool_use_id": tool.id, "content": content})
                        print(f"   [👁️ Claude está leyendo {tool.input['filepath']}]")
                    except Exception as e:
                        tool_results.append({"type": "tool_result", "tool_use_id": tool.id, "content": str(e), "is_error": True})
                
                # 🛠️ Claude quiere ESCRIBIR/EDITAR un archivo
                elif tool.name == 'escribir_archivo':
                    try:
                        # Crea la carpeta si no existe
                        os.makedirs(os.path.dirname(tool.input['filepath']) or '.', exist_ok=True)
                        with open(tool.input['filepath'], 'w') as f:
                            f.write(tool.input['content'])
                        tool_results.append({"type": "tool_result", "tool_use_id": tool.id, "content": "Archivo editado y guardado exitosamente en el disco."})
                        print(f"\n   ✅ [ACCIÓN EXITOSA]: Claude modificó el código de -> {tool.input['filepath']}")
                    except Exception as e:
                        tool_results.append({"type": "tool_result", "tool_use_id": tool.id, "content": str(e), "is_error": True})
                
                # 🛠️ Claude quiere EXPLORAR una carpeta
                elif tool.name == 'explorar_carpeta':
                    try:
                        files = os.listdir(tool.input['directory'])
                        tool_results.append({"type": "tool_result", "tool_use_id": tool.id, "content": ", ".join(files)})
                        print(f"   [🔍 Claude exploró la carpeta {tool.input['directory']}]")
                    except Exception as e:
                        tool_results.append({"type": "tool_result", "tool_use_id": tool.id, "content": str(e), "is_error": True})
            
            # Le devolvemos a Claude el resultado de su acción
            messages.append({"role": "user", "content": tool_results})

    except KeyboardInterrupt:
        print("\nSaliendo...")
        break
    except Exception as e:
        print(f"\n❌ Error de red o API: {e}")
        messages.pop() # Quitamos el mensaje erróneo para poder reintentar
