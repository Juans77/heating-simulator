[readme.md](https://github.com/user-attachments/files/24846235/readme.md)
# Prompt for Automatic Project Documentation (README.md)

Use this prompt with an AI tool (e.g., Antigravity or similar) while the project folder is open.

---

## Instruction

Analyze the **entire project**, including all folders and files.

Generate a **technical `README.md`** suitable for a GitHub repository.

The documentation must be based **only on the existing code and structure**. Do **not** invent features or behavior that are not present in the codebase.

Output must be **valid Markdown only**.

---

## Required Sections

Include the following sections in the generated README:

### 1. System Overview
Provide a concise description of what the system does and its main purpose.

### 2. Architecture and Folder Structure
Describe the project structure and explain the role of the main folders and files.

### 3. Control Logic Explanation
Explain how the core logic works, step by step, based strictly on the code.

### 4. MQTT Protocol Interface
Document:
- Topics used for input
- Topics used for output
- Example JSON payloads (only if clearly defined in the code)

### 5. Configuration (Environment Variables)
List and explain all environment variables used by the system and their purpose.

### 6. Auto-Tuning Algorithm
Explain how the auto-tuning mechanism works, including:
- What parameters are tuned
- Which metrics are used
- When tuning is triggered

### 7. Persistence Mechanism
Describe what data is persisted (if any), where it is stored, and why.

### 8. Run Instructions
Explain how to run the project based on the existing configuration:
- Requirements
- Build and startup process
- Docker usage (if present in the code)

### 9. Limitations
List known limitations inferred from the code structure or logic.

### 10. Future Work
Suggest realistic future improvements based on the current design.

---

## Constraints

- Do **not** hallucinate or assume missing features.
- Do **not** add external concepts not present in the code.
- Base every explanation strictly on the analyzed source files.
- Output only the generated `README.md` in valid Markdown format.

