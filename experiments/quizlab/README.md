---
name: quizlab
display_name: Quiz Lab
description: Author quizzes in markdown with LaTeX, code blocks, and multiple question types. Server-side grading keeps answers secure.
version: "1.0.0"
entry_point: quiz.html
leap_version: ">=1.0"
require_registration: true
pages:
  - {name: "Scores", file: "scores.html", admin: true}
---

# Quiz Lab

Create and take quizzes written in markdown. Supports single-select (radio), multi-select (checkbox), and numeric answer types. Questions can include LaTeX math, code blocks, and rich formatting.

Grading is server-side so students cannot inspect correct answers in the browser.

## Functions

- **`list_quizzes()`** — Returns available quiz files and their titles.
- **`get_quiz(quiz_file)`** — Returns quiz content with correct answers stripped.
- **`grade(student_id, quiz_file, question_id, answer)`** — Grades a single question, saves the submission privately, and returns the result. Uses `@nolog` so submissions never appear in the platform's public logs.
- **`get_my_submissions(student_id, quiz_file)`** — Returns the student's latest submission per question from private storage.
- **`get_all_scores(quiz_file)`** — Returns all student scores for a quiz. Admin only (`@adminonly`). Used by the Scores page.

Browse all functions at `/static/functions.html?exp=quizlab`.

## Quiz Format

Place `.md` files in `quiz/`. Each file uses YAML frontmatter followed by questions separated by `---`.

### Frontmatter

```yaml
---
title: My Quiz
allow_resubmit: false
show_result: true
---
```

| Field | Required | Default | Description |
|---|---|---|---|
| `title` | yes | filename | Display title for the quiz |
| `allow_resubmit` | no | `false` | If `true`, students can re-answer questions after submitting |
| `show_result` | no | `true` | If `false`, students see "Answer recorded" instead of correct/incorrect feedback after submitting |

### Question structure

Every question starts with a level-2 heading that includes a number, a unique ID, and an optional point value in brackets:

```markdown
## Question 1: my_id [5]
```

- The number is for display only; the **ID** (`my_id`) is what the grading system uses.
- The point value `[5]` is optional and defaults to 1 if omitted. Points are shown in the UI and included in the grading result.
- IDs must be unique within a quiz, contain no spaces, and use only letters, numbers, hyphens, or underscores.
- Everything between the heading and the first choice/answer line is the **question body** — full markdown with LaTeX (`$...$`), code blocks, images, etc.
- An optional blockquote (`> ...`) after the choices is the **explanation**, shown to the student after they submit.
- Separate questions with `---` (horizontal rule).

### Multiple choice (single answer)

Use `( )` for wrong choices and `(x)` for the one correct choice. Rendered as radio buttons — only one can be selected.

```markdown
## Question 1: derivatives [2]

What is $\frac{d}{dx}(x^3)$?

- ( ) $2x$
- (x) $3x^2$
- ( ) $x^3$

> Apply the power rule: $\frac{d}{dx}(x^n) = nx^{n-1}$.
```

### True / False

A true/false question is just a two-choice multiple choice:

```markdown
## Question 2: gravity

The acceleration due to gravity on Earth is approximately $9.8 \text{ m/s}^2$.

- (x) True
- ( ) False

> $g \approx 9.8 \text{ m/s}^2$ at sea level.
```

### Multiple choice (multiple answers)

Use `[ ]` for wrong choices and `[x]` for correct ones. Rendered as checkboxes — the student must select all correct answers and no wrong ones to get credit.

```markdown
## Question 3: trig

Which are trigonometric functions?

- [ ] $\ln(x)$
- [x] $\sin(x)$
- [x] $\cos(x)$
- [ ] $e^x$

> sin and cos are the fundamental trig functions.
```

### Numeric answer

Use `= value` where `value` is the expected number. Rendered as a text input box. Graded with floating-point tolerance (`1e-6` absolute or relative, whichever is larger), so students don't need to match every decimal.

```markdown
## Question 4: compute [5]

Compute $\frac{d}{dx}(3x^4)$ at $x = 1$.

= 12

> $\frac{d}{dx}(3x^4) = 12x^3$. At $x = 1$: $12 \cdot 1^3 = 12$.
```

### Full example

```markdown
---
title: Calculus Basics
allow_resubmit: false
---

## Question 1: power_rule [2]

What is $\frac{d}{dx}(x^5)$?

- ( ) $4x^4$
- (x) $5x^4$
- ( ) $x^4$

> Power rule: bring down the 5, reduce exponent by 1.

---

## Question 2: is_linear

The function $f(x) = 3x + 1$ is linear.

- (x) True
- ( ) False

---

## Question 3: even_functions

Select all even functions:

- [x] $\cos(x)$
- [ ] $\sin(x)$
- [x] $x^2$
- [ ] $x^3$

> Even functions satisfy $f(-x) = f(x)$.

---

## Question 4: evaluate

Evaluate $\int_0^1 2x \, dx$.

= 1

> $\int_0^1 2x \, dx = [x^2]_0^1 = 1 - 0 = 1$.
```

## Admin Scores Page

The [Scores page](scores.html) is an admin-only view showing all student scores in a table. Select a quiz to see each student's per-question results with color coding (green = correct, red = incorrect, dash = unanswered). The total column shows points earned with a percentage. Click **Export CSV** to download the scores.

This page only appears in the navbar for authenticated admin sessions (via the `pages` frontmatter config).

## Student Workflow

1. Open the [quiz page](quiz.html) and enter your student ID.
2. Select a quiz from the dropdown. The student ID field is masked once a quiz is loaded.
3. Read each question, select or type your answer, and click **Submit**.
4. See immediate feedback: correct/incorrect and an explanation (unless the quiz has `show_result: false`, in which case only "Answer recorded" is shown).
5. A score bar below the title tracks your progress (hidden when `show_result: false`).
6. Previously submitted answers are saved and shown on reload.

## Security and Privacy

- **Quiz files are not publicly served.** Quiz markdown files (containing correct answers) are stored in `quiz/`, outside the `ui/` directory, so they cannot be accessed via the browser.
- **Server-side grading.** Correct answers are never sent to the client. The `get_quiz()` function strips all answer markers before returning quiz content.
- **Result redaction.** When `show_result: false`, the `grade()` function returns only `{submitted: true}` — the correct answer, expected value, and explanation are not included in the response.
- **Private submission storage.** `grade()` uses `@nolog` so submissions never enter the platform's public log system. Instead, submissions are stored privately in `data/{student_id}.jsonl` (one JSON record per line). Each record contains `student_id`, `quiz_file`, `question_id`, `answer`, and the full grading `result`. The `get_my_submissions()` function reads only the requesting student's file.
- **Student ID masking.** The student ID input is masked once a quiz is selected, preventing casual shoulder-surfing.
- **FERPA considerations.** For FERPA-sensitive deployments, use anonymous opaque tokens (e.g., random strings) as student IDs instead of real names or school IDs. The token acts as both identity and authentication — without it, no one can access or impersonate a student's submissions. The instructor maintains the token-to-student mapping offline.
