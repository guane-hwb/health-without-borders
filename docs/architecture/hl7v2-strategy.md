# ADR: Estrategia de Almacenamiento e Integración HL7v2

## 1. Contexto

El sistema "UNICEF Border Health" requiere interoperabilidad con sistemas de salud heredados (como el SIP - Sistema de Información Perinatal) utilizando el estándar internacional HL7v2. Actualmente, el proyecto utiliza la API de Google Cloud Healthcare como almacén de mensajes. Sin embargo, por políticas de mitigación de *Vendor Lock-in* (dependencia de proveedor) y optimización de costos a largo plazo, se evalúan alternativas de arquitectura.

---

## 2. Opciones Evaluadas

### Opción A: Managed Cloud Service (Google Cloud Healthcare API)

*Estado actual del proyecto.* Se utiliza el componente `hl7V2Stores` de Google Cloud para ingerir, validar y almacenar los mensajes generados por el backend en FastAPI.

**Pros (Ventajas):**

* **Cero Mantenimiento (Serverless):** Google gestiona la infraestructura, los parches de seguridad y la alta disponibilidad.
* **Cumplimiento Legal Inmediato:** Certificación HIPAA out-of-the-box, garantizando trazabilidad y auditoría de accesos inmutable.
* **Ecosistema Moderno:** Permite la conversión automática de los mensajes HL7v2 heredados al estándar moderno FHIR en tiempo real.
* **Event-Driven:** Integración nativa con Google Pub/Sub para notificar a otros sistemas cuando llega un paciente nuevo.

**Contras (Desventajas):**

* **Vendor Lock-in:** Alta dependencia del ecosistema de Google. Migrar los datos en el futuro requiere un esfuerzo de exportación.
* **Costos Operativos (Opex):** Aunque económico al inicio, se cobra por cada mensaje ingerido, almacenado y consultado de por vida.
* **Caja Negra:** No hay una interfaz gráfica nativa para que un administrador médico lea los mensajes HL7 directamente desde la consola.

---

### Opción B: Motor de Integración Open Source (NextGen / Mirth Connect)

Desplegar un contenedor propio con *Mirth Connect*, el estándar de la industria hospitalaria de código abierto para enrutamiento y traducción de mensajes en salud.

**Pros (Ventajas):**

* **Soberanía de Datos Total:** Los datos clínicos nunca salen de la infraestructura controlada por el proyecto (ideal para normativas estrictas de ONGs/Gobierno).
* **Licencia Gratuita:** El software base es open-source, sin costo por volumen de mensajes.
* **Soporte de Protocolos Heredados:** Es el único de la lista capaz de conectarse directamente a hospitales antiguos usando el protocolo MLLP (TCP/IP), no solo REST/HTTP.
* **Interfaz Gráfica (Dashboard):** Permite a los técnicos ver el flujo de mensajes, errores y reintentar envíos fallidos visualmente.

**Contras (Desventajas):**

* **Costo de Infraestructura y Mantenimiento:** Aunque el software es gratis, requiere un servidor dedicado (VPS, EC2 o Cloud Run con volúmenes persistentes) que debe ser pagado y mantenido por el equipo.
* **Curva de Aprendizaje:** Requiere capacitar al equipo en un software especializado basado en Java/Rhino.
* **Seguridad autogestionada:** El equipo es responsable de configurar cifrados, copias de seguridad y auditorías.

---

### Opción C: Desarrollo In-House (PostgreSQL + Librería Python)

Eliminar servicios de terceros y motores externos. El backend de FastAPI actual valida el mensaje usando librerías como `hl7apy` y guarda el texto crudo en una tabla dedicada (`hl7_audit_logs`) en la base de datos `unicef-db-dev`.

**Pros (Ventajas):**

* **Arquitectura Simplificada:** No hay piezas móviles nuevas. Todo vive dentro de la infraestructura que el equipo ya domina (PostgreSQL + Cloud Run).
* **Costo Adicional Cero:** Se aprovecha la base de datos ya provisionada. No hay cargos por API ni servidores extra.
* **Cero Dependencia:** Control absoluto del código y los datos.

**Contras (Desventajas):**

* **No es un verdadero Motor de Integración:** Sirve como repositorio histórico (auditoría), pero no puede enrutar, alertar o retransmitir mensajes a otros hospitales automáticamente.
* **Reinventar la Rueda:** Construir conversores de HL7 a JSON o a FHIR requiere meses de desarrollo in-house, esfuerzo que motores como Google o Mirth ya tienen resuelto.
* **Escalabilidad Limitada:** A largo plazo, almacenar millones de cadenas de texto HL7 puede degradar el rendimiento de la base de datos transaccional principal si no se particiona adecuadamente.

---

## 3. Matriz de Resumen

| Característica | Opción A (Google Cloud) | Opción B (Mirth Connect) | Opción C (In-House Postgres) |
| --- | --- | --- | --- |
| **Costo Inicial de Setup** | Bajo | Alto (Configurar servidor) | Medio (Crear tablas/código) |
| **Costo a Largo Plazo** | Pago por uso (Variable) | Fijo (Servidor) | Incluido en BD actual |
| **Nivel de Mantenimiento** | Ninguno | Alto | Bajo |
| **Evita Vendor Lock-in** | No | Sí | Sí |
| **Conversión a FHIR** | Automática | Requiere desarrollo/plugins | No disponible |

## 4. Recomendación Estratégica

Para la etapa actual (Milestone 2/3 - Piloto de Interoperabilidad), la **Opción A (Google Cloud)** es la más pragmática para demostrar valor rápidamente sin desviar recursos de ingeniería hacia el mantenimiento de servidores.

Si en futuras fases (Escalamiento Nacional) el Ministerio de Salud exige conexión MLLP local o autonomía total en servidores gubernamentales, la arquitectura modular actual permite reemplazar el endpoint de Google por un despliegue de la **Opción B (Mirth Connect)** sin afectar el resto de la aplicación.

