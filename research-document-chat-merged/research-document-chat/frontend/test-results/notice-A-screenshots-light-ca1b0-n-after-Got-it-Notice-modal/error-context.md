# Instructions

- Following Playwright test failed.
- Explain why, be concise, respect Playwright best practices.
- Provide a snippet of code with the fix, if possible.

# Test info

- Name: notice.spec.ts >> A screenshots light: new conversation, after Got it, Notice modal
- Location: e2e\notice.spec.ts:148:3

# Error details

```
Test timeout of 240000ms exceeded.
```

# Page snapshot

```yaml
- generic [ref=e3]:
  - generic [ref=e4]:
    - generic [ref=e5]:
      - generic [ref=e10]:
        - paragraph [ref=e11]: Digital MSL
        - paragraph [ref=e12]: Therapeutic area science
      - link "Sciagen (opens sciagen.ai)" [ref=e13] [cursor=pointer]:
        - /url: https://sciagen.ai
        - img "Sciagen" [ref=e14]
    - generic [ref=e16]:
      - heading "Digital MSL for therapeutic-area science, with sources you can check" [level=1] [ref=e17]
      - paragraph [ref=e18]: Ask a clinical question and get an answer cited to approved repositories and scientific literature. Questions the AI is not confident about go to a Physical MSL.
    - generic [ref=e19]:
      - generic [ref=e20]: For authorised clinical users
      - generic [ref=e21]:
        - text: Powered by
        - link "Sciagen (opens sciagen.ai)" [ref=e22] [cursor=pointer]:
          - /url: https://sciagen.ai
          - img "Sciagen" [ref=e23]
        - link "sciagen.ai" [ref=e24] [cursor=pointer]:
          - /url: https://sciagen.ai
  - generic [ref=e26]:
    - button "Switch to dark mode" [ref=e27] [cursor=pointer]
    - heading "Sign in" [level=2] [ref=e30]
    - paragraph [ref=e31]: Choose your role to continue.
    - group "Role" [ref=e32]:
      - button "Doctor Ask clinical questions and get cited answers." [pressed] [ref=e33] [cursor=pointer]:
        - generic [ref=e39]:
          - generic [ref=e40]: Doctor
          - generic [ref=e41]: Ask clinical questions and get cited answers.
      - button "Physical MSL Review questions escalated by the AI." [ref=e42] [cursor=pointer]:
        - generic [ref=e47]:
          - generic [ref=e48]: Physical MSL
          - generic [ref=e49]: Review questions escalated by the AI.
      - button "Administrator Manage users, sources and settings." [ref=e50] [cursor=pointer]:
        - generic [ref=e54]:
          - generic [ref=e55]: Administrator
          - generic [ref=e56]: Manage users, sources and settings.
    - generic [ref=e57]:
      - paragraph [ref=e58]: "Demo mode: sample credentials filled in"
      - generic [ref=e59]:
        - generic [ref=e60]:
          - generic [ref=e61]: Name
          - textbox "Name" [ref=e62]: Demo Doctor
        - generic [ref=e63]:
          - generic [ref=e64]: Work email
          - textbox "Work email" [ref=e65]: doctor@demo.example
      - generic [ref=e66]:
        - generic [ref=e67]: Password
        - generic [ref=e68]:
          - textbox "Password" [ref=e69]: DemoMSL#2026
          - button "Show password" [ref=e70] [cursor=pointer]
      - alert [ref=e74]: Failed to fetch
      - button "Continue as Doctor" [ref=e77] [cursor=pointer]
    - generic [ref=e80]:
      - generic [ref=e81]:
        - paragraph [ref=e82]: New to Digital MSL?
        - paragraph [ref=e83]: Doctors can create their own account and choose their therapeutic areas.
      - button "Doctor? Register here" [ref=e84] [cursor=pointer]
    - paragraph [ref=e85]: Authorised users only; sign-in activity is recorded in the audit trail.
```