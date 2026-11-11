# Merge provenance

This standalone package uses the Antigravity `research-document-chat.zip` as the base. It incorporates missing frontend support modules from the earlier `research-document-chat-extraction.zip` and original `gxp-doc-ai-feature-gxp-doc-ai.zip` source project.

## Included workflow
- Transient PubMed and ClinicalTrials.gov search
- Selected research-record import into a configured repository
- DMS upload and indexing integration
- Repository-scoped RAG chat and persisted chat history
- Citation extraction and validation against retrieved document/page metadata

## Key integration repairs
- Fixed frontend import component contract and toast provider setup
- Added missing `ToastContext`, `pageTheme`, `tokenUtils`, and `types/auth.ts`
- Added connector settings metadata to the standalone repository service
- Fixed frontend search/import API paths to use `/api/v1` endpoints
- Fixed connector `.env` lookup path and aligned environment variable names
- Added numeric page citation validation and a regression test
- Removed generated Python bytecode/cache files

See `README.md` for setup instructions and the precise verification limitations.
