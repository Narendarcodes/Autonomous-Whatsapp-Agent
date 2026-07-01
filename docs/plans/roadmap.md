## The Roadmap

The implementation of Hermes as the core reasoning engine allows us to rapidly expand functionality beyond calendar management by simply exposing new MCP tools.

The long-term roadmap focuses on turning WhatsApp into a "conversational operating system":

- **Phase 1: Calendar & Scheduling (Current)**
  - Natural language event creation.
  - Proactive reminders & conflict detection.
- **Phase 2: Document & Media Management**
  - Cloud storage integration (Drive, Dropbox).
  - OCR extraction from images and PDFs.
  - Receipt and tax document organization.
  - Exporting chat history to structured Google Docs.
- **Phase 3: Financial & Expense Tracking**
  - Processing receipt images.
  - Generating categorized monthly spending reports.
- **Phase 4: Communication & Collaboration**
  - Email drafting/sending via WhatsApp.
  - Group chat action-item extraction and summaries.
  - Voice memo transcription to notes/tasks.
- **Phase 5: The "Second Brain" & Personal Chief of Staff**
  - Maintain contextual memory of user preferences (Hermes native).
  - Proactive life-management (renewals, follow-ups).
  - Natural-language automation triggers ("Save internship emails to Drive").

*Note: All features must pass through the `permission_service.py` to maintain DPDP compliance and ensure user consent for high-risk actions.*
