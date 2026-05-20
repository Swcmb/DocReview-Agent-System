You are an elite AI systems architect and full-stack agent engineer.

Your task is to build a production-grade multi-agent AI system inside the CURRENT FOLDER using a real agent framework (prefer LangGraph + MCP architecture).

The system to build is named:

# DocReview Agent System

This system MUST implement:
- A Supervisor/Main Agent
- A DocReview Reviewer Sub-Agent
- Iterative review loops
- Sequential Thinking
- Context7 MCP integration
- Tool calling
- Human approval gating
- Autonomous document revision cycles

The system MUST be fully runnable and engineered as a real project, not a conceptual demo.

==================================================
# PRIMARY OBJECTIVE
==================================================

The system workflow MUST be:

1. User provides a task/request.

2. The Supervisor Agent first generates or updates a SPECIFICATION DOCUMENT.

3. The specification document is sent to the DocReview sub-agent.

4. The DocReview sub-agent repeatedly reviews the specification document.

5. The Supervisor Agent revises the specification according to review feedback.

6. The review loop continues UNTIL:
   - the reviewer fully agrees
   - review conclusion becomes "Pass"

7. ONLY AFTER PASS:
   ask the user:

   "The specification has passed review. Start execution?"

8. ONLY AFTER explicit user approval:
   begin actual task execution.

The reviewer MUST behave strictly and independently.

The reviewer MUST NEVER directly rewrite the document.

The reviewer ONLY:
- discovers problems
- outputs review reports
- classifies severity
- provides actionable revision suggestions

==================================================
# REQUIRED FRAMEWORK
==================================================

Use:
- LangGraph (preferred)
- Python
- MCP-compatible architecture
- Stateful workflow orchestration

The project MUST be modular and production-oriented.

==================================================
# REQUIRED AGENT ARCHITECTURE
==================================================

Build:

Supervisor Agent
│
├── Planner
├── Specification Generator
├── Revision Engine
├── Execution Controller
│
└── DocReview Sub-Agent
    ├── Sequential Thinking
    ├── Consistency Analyzer
    ├── Risk Detector
    ├── Feasibility Reviewer
    ├── Executability Validator
    └── Context7 Context Engine

==================================================
# REQUIRED MCP SERVERS
==================================================

The system MUST integrate:

1. Sequential Thinking MCP Server
Purpose:
- multi-step reasoning
- structured review decomposition
- iterative thinking chains

2. Context7 MCP Server
Purpose:
- long-context retrieval
- cross-document linking
- dependency tracing
- contextual review

==================================================
# REQUIRED BUILT-IN TOOLS
==================================================

The system MUST include these built-in tools:

## 1. Reading Tool
Purpose:
- retrieve files
- inspect files
- search documents
- read PRD/specification files
- perform diff analysis

Capabilities:
- read_file
- search_text
- list_directory
- compare_versions

## 2. Terminal Tool
Purpose:
- execute terminal commands
- inspect environments
- run validation commands
- obtain execution status/results

Capabilities:
- run bash commands
- capture stdout/stderr
- return exit codes
- support retry/error handling

## 3. Web Search Tool
Purpose:
- search webpages related to user tasks
- validate APIs/frameworks
- detect outdated dependencies
- retrieve engineering references

==================================================
# REQUIRED REVIEW PIPELINE
==================================================

The DocReview sub-agent MUST internally execute this STRICT six-step review process:

1. Extract Core Closed Loop
2. Consistency Check
3. Requirement Atomization and Completeness
4. Technical Feasibility Deduction
5. Risk Detection and Fallback Deduction
6. Executability Review

==================================================
# REQUIRED REVIEW RULES
==================================================

Review severity levels:
- Blocking
- High
- Medium
- Low

Review conclusions:
- Pass
- Conditional Pass
- Fail

Pass requirements:
- No Blocking issues
- Critical flows validated
- No unresolved severe ambiguity
- Acceptance criteria coverage complete

Reviewer MUST:
- reject vague requirements
- reject missing fallback plans
- reject incomplete acceptance criteria
- reject contradictory terminology
- reject incomplete execution paths

==================================================
# REQUIRED ITERATIVE REVIEW SYSTEM
==================================================

The system MUST support:

- repeated review loops
- revision comparison
- unresolved issue tracking
- fixed issue tracking
- persistent review history

The Supervisor Agent MUST:
- automatically revise documents
- re-submit revised versions
- continue until review passes

The system MUST maintain:
- review iteration count
- review history
- issue states
- revision summaries

==================================================
# REQUIRED EXECUTION GATING
==================================================

The system MUST NEVER execute tasks immediately.

Mandatory workflow:

Generate Spec
→ Review
→ Revise
→ Re-review
→ Pass
→ Ask User
→ Execute

The user approval step is REQUIRED.

==================================================
# REQUIRED PROJECT STRUCTURE
==================================================

Generate a clean production-grade directory structure.

Example:

project/
│
├── agents/
│   ├── supervisor/
│   └── docreview/
│
├── workflows/
│
├── mcp/
│
├── tools/
│
├── memory/
│
├── schemas/
│
├── reviews/
│
├── docs/
│
├── state/
│
└── main.py

==================================================
# REQUIRED IMPLEMENTATION DETAILS
==================================================

Generate:
- architecture design
- LangGraph workflow
- state definitions
- MCP integration layer
- review loop orchestration
- structured extraction schema
- review report schema
- execution gating logic
- retry/failure handling
- persistent memory strategy

The implementation MUST support:
- multi-document review
- future multi-reviewer expansion
- diff-aware re-review
- human-in-the-loop approval

==================================================
# REQUIRED OUTPUT STYLE
==================================================

Output MUST be:
- implementation-oriented
- production-grade
- engineering-focused
- explicit
- executable
- modular

Avoid:
- pseudo-philosophical explanations
- toy demos
- vague abstractions
- incomplete workflows

Prioritize:
- deterministic workflows
- strict review logic
- agent coordination
- scalability
- maintainability
- review reliability

Treat this as a REAL enterprise AI governance and engineering review system.
