import json
import os
import re
import requests

from src.utils.config_manager import ConfigManager


class JobFilterPipeline2:
    """
    Multi-stage job filter pipeline.

    Stages (in order):
      normalize → dedup → hard_veto → experience_filter → desc_red_flag
      → title_filter → company_veto → tag_presort → ai_score → rank → select

    All veto lists and the candidate's stack are loaded from config/veto.json
    at construction time, so they can be edited without touching source code.

    The AI scoring prompt injects the content of user_prompt.md as the
    CANDIDATE section, making the profile fully customisable.

    The AI provider (OpenAI or OpenRouter), model, and key are resolved
    through ConfigManager, so switching to a different model or provider
    requires only a config.json / .env change.
    """

    # ── Role-agnostic structural filters (not moved to veto.json) ─────────────
    # These describe job *types* to exclude regardless of personal preference.

    SOFTWARE_KEYWORDS = {
        "software", "developer", "engineer", "engineering",
        "backend", "full stack", "fullstack",
        "python", "node", "nodejs", "javascript", "typescript",
        "django", "fastapi", "flask", "golang",
        "devops", "cloud", "sre", "platform",
        "api", "microservices", "infrastructure",
        "tech lead", "sde", "swe", "mts", "programmer", "react",
    }

    FRONTEND_VETO_KEYWORDS = {
        "frontend", "front-end", "front end",
        "angular", "vue", "ui developer", "ui engineer",
        "android", "ios", "flutter", "mobile", "kotlin", "swift",
        "embedded", "firmware",
        "ml engineer", "data engineer", "data scientist",
        "intern", "internship", "lead", "golang", "mlops", "devops",
        "web developer - java technologies",
        "member of technical staff - mts",
    }

    # Cheap regex pre-checks on description before hitting the AI.
    DESC_RED_FLAGS = {
        "walk-in":      r"walk.?in|walkin",
        "venue listed": r"venue\s*:|interview venue|bring your resume|carry your resume",
    }

    def __init__(
        self,
        openai_api_key=None,       # legacy param — ignored when config_manager is provided
        cache_file="score_cache.json",
        daily_apply_limit=None,
        min_apply_score=None,
        ai_score_limit=None,
        batch_size=None,
        config_manager: ConfigManager = None,
    ):
        if config_manager is None:
            config_manager = ConfigManager()
        self.cfg = config_manager

        ai_cfg = self.cfg.config.get("ai", {})
        self.data_roles = self.cfg.config.get("profile", {}).get("role_focus") == "data_science"

        # Explicit params override config (backward compat).
        self.cache_file        = "score_cache_data_science_v1.json" if self.data_roles and cache_file == "score_cache.json" else cache_file
        self.daily_apply_limit = daily_apply_limit if daily_apply_limit is not None else ai_cfg.get("daily_apply_limit", 50)
        self.min_apply_score   = min_apply_score   if min_apply_score   is not None else ai_cfg.get("score_threshold",   50)
        self.ai_score_limit    = ai_score_limit    if ai_score_limit    is not None else ai_cfg.get("ai_score_limit",    300)
        self.batch_size        = batch_size        if batch_size        is not None else ai_cfg.get("batch_size",        50)
        self.cache             = self._load_cache()

        # Load veto lists from veto.json
        veto = self.cfg.veto
        self.VETO_TITLES    = [t.lower() for t in veto.get("titles",    [])]
        self.VETO_COMPANIES = {c.lower() for c in veto.get("companies", [])}

        # Candidate's stack from config (drives tag_presort overlap score)
        raw_stack = self.cfg.config.get("profile", {}).get("tech_stack", [])
        self.MY_STACK = [t.lower() for t in raw_stack] if raw_stack else self._default_stack()

    @staticmethod
    def _default_stack() -> list[str]:
        return [
            "node", "node.js", "nodejs", "python", "javascript", "typescript",
            "express", "express.js", "fastapi", "flask", "nestjs", "django",
            "mongodb", "mongoose", "postgresql", "mysql", "redis", "dynamodb",
            "aws", "gcp", "docker", "kubernetes", "ci/cd", "github actions", "terraform",
            "rest", "rest api", "graphql", "websocket", "kafka",
            "selenium", "playwright", "langchain", "openai", "llm", "rag",
            "git", "postman", "jwt", "microservices",
        ]

    # =========================================================
    # MAIN
    # =========================================================

    def run(self, jobs):
        jobs = self.filter_candidates(jobs)
        jobs = self.ai_score_batch(jobs)
        jobs = self.rank(jobs)
        jobs = self.select(jobs)
        for j in jobs:
            print(f"  {j.get('ai_score'):>3}  {j.get('title')} @ {j.get('company')} | {j.get('ai_reason', '')}")
        return jobs

    def filter_candidates(self, jobs):
        """Apply the same role/profile filters whether or not AI is enabled."""
        def _drop(before, after, label):
            d = before - after
            return f"{label}:{d}" if d else None

        raw = len(jobs)
        jobs = self.normalize_jobs(jobs)

        n = len(jobs); jobs = self.dedup(jobs);               d_dedup   = n - len(jobs)
        n = len(jobs); jobs = self.hard_veto(jobs);           d_veto    = n - len(jobs)
        n = len(jobs); jobs = self.experience_filter(jobs);   d_exp     = n - len(jobs)
        n = len(jobs); jobs = self.desc_red_flag_check(jobs); d_flags   = n - len(jobs)
        n = len(jobs); jobs = self.title_filter(jobs);        d_title   = n - len(jobs)
        n = len(jobs); jobs = self.company_veto(jobs);        d_company = n - len(jobs)

        jobs = self.tag_presort(jobs)
        jobs = jobs[:self.ai_score_limit]

        dropped = [
            (d_dedup,   "dedup"),
            (d_veto,    "veto"),
            (d_exp,     "exp"),
            (d_flags,   "flags"),
            (d_title,   "title"),
            (d_company, "company"),
        ]
        breakdown = "  ".join(f"-{d} {lbl}" for d, lbl in dropped if d > 0) or "none filtered"
        print(f"  {raw} fetched  →  {len(jobs)} to score   ({breakdown})")

        return jobs

    # =========================================================
    # NORMALIZE
    # =========================================================

    def normalize_jobs(self, jobs):
        normalized = []

        for j in jobs:
            job = j if isinstance(j, dict) else j.__dict__

            # days old
            posted   = (job.get("posted_date") or "").lower()
            days_old = 7
            if "today" in posted or "hour" in posted or "just now" in posted:
                days_old = 0
            elif "yesterday" in posted:
                days_old = 1
            else:
                m = re.search(r"(\d+)\s*day", posted)
                if m:
                    days_old = int(m.group(1))
                else:
                    m = re.search(r"(\d+)\s*week", posted)
                    if m:
                        days_old = int(m.group(1)) * 7

            # experience range
            exp = job.get("experience") or ""
            exp_min, exp_max = 0, 10
            nums = re.findall(r"\d+", exp)
            if len(nums) >= 2:
                exp_min, exp_max = int(nums[0]), int(nums[1])
            elif len(nums) == 1:
                exp_min = exp_max = int(nums[0])

            # tags
            raw_tags = job.get("tags") or job.get("skills") or []
            if isinstance(raw_tags, str):
                raw_tags = re.split(r"[,;|]", raw_tags)
            tags = [t.strip().lower() for t in raw_tags if t.strip()]

            normalized.append({
                "job_id":         job.get("job_id"),
                "title":          (job.get("title")       or "").strip(),
                "company":        (job.get("company")     or "").strip(),
                "location":       (job.get("location")    or "").strip(),
                "description":    (job.get("description") or "").strip(),
                "tags":           tags,
                "mandatory_tags": tags[:2],
                "optional_tags":  tags[2:],
                "days_old":       days_old,
                "experience_min": exp_min,
                "experience_max": exp_max,
            })

        return normalized

    # =========================================================
    # DEDUP
    # =========================================================

    def dedup(self, jobs):
        seen, result = set(), []
        for j in jobs:
            job_id = j.get("job_id")
            if job_id is None:
                result.append(j)
                continue
            if job_id in seen:
                continue
            seen.add(job_id)
            result.append(j)
        return result

    # =========================================================
    # HARD VETO — title only, veto.json-driven
    # =========================================================

    def hard_veto(self, jobs):
        clean = []
        for j in jobs:
            title = (j.get("title") or "").lower()
            if any(kw in title for kw in self.VETO_TITLES):
                continue
            clean.append(j)
        return clean

    # =========================================================
    # EXPERIENCE FILTER
    # =========================================================

    def experience_filter(self, jobs):
        if self.data_roles:
            profile = self.cfg.config.get("profile", {})
            max_minimum = float(profile.get("experience_years", 0)) + float(
                self.cfg.config.get("search", {}).get("experience_flexibility_years", 1)
            )
            return [j for j in jobs if j.get("experience_min", 0) <= max_minimum
                    and not (float(profile.get("experience_years", 0)) < 3
                             and re.search(r"\b(senior|sr|lead|principal|staff|head|director|chief)\b", j.get("title", ""), re.I))]
        return [
            j for j in jobs
            if j.get("experience_min", 0) <= 4
            and j.get("experience_max", 10) > 0
        ]

    # =========================================================
    # DESC RED FLAG CHECK
    # =========================================================

    def desc_red_flag_check(self, jobs):
        clean = []
        for j in jobs:
            desc    = (j.get("description") or "").lower()
            flagged = [
                label for label, pat in self.DESC_RED_FLAGS.items()
                if re.search(pat, desc)
            ]
            if flagged:
                continue
            clean.append(j)
        return clean

    # =========================================================
    # TITLE FILTER
    # =========================================================

    def title_filter(self, jobs):
        if self.data_roles:
            return [j for j in jobs if self._is_data_role(j)]
        result = []
        for j in jobs:
            title = (j.get("title") or "").lower()
            if not any(kw in title for kw in self.SOFTWARE_KEYWORDS):
                continue
            if any(kw in title for kw in self.FRONTEND_VETO_KEYWORDS):
                continue
            result.append(j)
        return result

    @staticmethod
    def _is_data_role(job):
        # Word boundaries avoid false matches such as AI in retail or ML in HTML.
        title = (job.get("title") or "").lower()
        if re.search(r"\b(sales|recruiter|recruitment|marketing|business development|data entry)\b", title):
            return False
        core = (
            r"\b(data scien\w*|machine[ -]learning|artificial intelligence|"
            r"ai|ml|gen[ -]?ai|generative ai|llms?|nlp|natural language processing|"
            r"deep[ -]learning|computer vision|ml[ -]?ops|ai[ -]?ops|"
            r"data analy\w*|business intelligence|bi (?:developer|engineer|analyst)|"
            r"data engineer\w*|analytics engineer\w*|decision scien\w*|"
            r"statistician|applied scien\w*)\b"
        )
        if re.search(core, title):
            return True
        # Generic engineering/research titles need explicit data/AI skills.
        generic = re.search(r"\b(software engineer|python developer|research engineer|research scientist|scientist)\b", title)
        tags = " ".join(job.get("tags", []))
        return bool(generic and re.search(core, tags))

    # =========================================================
    # COMPANY VETO — veto.json-driven
    # =========================================================

    def company_veto(self, jobs):
        clean = []
        for j in jobs:
            company = (j.get("company") or "").lower()
            if any(vc in company for vc in self.VETO_COMPANIES):
                continue
            clean.append(j)
        return clean

    # =========================================================
    # TAG PRESORT
    # =========================================================

    def tag_presort(self, jobs):
        my_stack = set(self.MY_STACK)

        def overlap(j):
            tags          = set(j.get("tags", []))
            mandatory_hit = sum(1 for t in j.get("mandatory_tags", []) if t in my_stack)
            total_hit     = len(tags & my_stack)
            recency_bonus = max(0, 7 - j.get("days_old", 7))
            return mandatory_hit * 3 + total_hit + recency_bonus

        return sorted(jobs, key=overlap, reverse=True)

    # =========================================================
    # AI SCORING
    # =========================================================

    def ai_score_batch(self, jobs):
        result = []
        for i in range(0, len(jobs), self.batch_size):
            batch  = jobs[i:i + self.batch_size]
            scores = self._call_ai(batch)
            for idx, job in enumerate(batch):
                jid  = str(job.get("job_id") or "")
                data = self.cache.get(jid) if jid else None

                if not data:
                    data = scores.get(str(idx), {"score": 0, "reason": "no response"})
                    if not isinstance(data.get("score"), int):
                        data = {"score": 0, "reason": "parse error"}
                    if jid:
                        self.cache[jid] = data

                job["ai_score"]  = data.get("score", 0)
                job["ai_reason"] = data.get("reason", "")
                result.append(job)

        self._save_cache()
        return result

    def _call_ai(self, jobs):
        # ── Build job block ───────────────────────────────────────────────────
        job_block = ""
        for i, j in enumerate(jobs):
            mandatory = ", ".join(j.get("mandatory_tags", [])) or "none"
            optional  = ", ".join(j.get("optional_tags",  [])) or "none"
            exp       = f"{j.get('experience_min', 0)}-{j.get('experience_max', 10)} yrs"
            job_block += (
                f"Job {i}:\n"
                f"  Title:     {j.get('title')}\n"
                f"  Company:   {j.get('company')}\n"
                f"  Mandatory: {mandatory}\n"
                f"  Optional:  {optional}\n"
                f"  Exp:       {exp}\n"
                f"  Days old:  {j.get('days_old', 7)}\n"
                f"---\n"
            )

        # ── Candidate profile from user_prompt.md ────────────────────────────
        user_profile = self.cfg.load_prompt()

        # ── Prompt ───────────────────────────────────────────────────────────
        prompt = f"""
You are a strict job filter. Score each job 0-100.
Be precise — avoid clustering scores at 85 or 60. Use the full range.

CANDIDATE:
{user_profile}

SCORING RUBRIC:
90-100: Direct match to the candidate's target role, known skills and experience.
75-89: Strong match to the target domain with a few secondary skill gaps.
55-74: Related role with useful skill overlap and realistic junior requirements.
30-54: Weak or ambiguous domain match, or substantial skill/experience gaps.
10-29: Mostly unrelated work or significant seniority mismatch.
0-9: Unrelated role or an explicit exclusion in the candidate profile.

RULES:
- Follow the candidate's target domain; do not prefer backend roles by default.
- For data science/AI/ML candidates, include model building, analysis, NLP,
  computer vision, GenAI/LLM/RAG, ML deployment and relevant data engineering.
- Python alone does not make a generic backend/web job a data science match.
- Treat technologies sought for a role separately from skills actually claimed.
- Never invent experience, qualifications or proficiency in unlisted tools.
- Judge experience against the saved candidate profile, not a fixed two years.
- Missing job information lowers confidence; return a concise reason.

Return ONLY valid JSON, no explanation outside it:
{{
  "0": {{"score": 92, "reason": "Direct target-role and experience match"}},
  "1": {{"score": 0,  "reason": "Unrelated role with no domain overlap"}}
}}

Jobs:
{job_block}
"""

        # ── API call ─────────────────────────────────────────────────────────
        try:
            res = requests.post(
                self.cfg.get_ai_url(),
                headers=self.cfg.get_ai_headers(),
                json={
                    "model":       self.cfg.config["ai"]["model"],
                    "messages":    [{"role": "user", "content": prompt}],
                    "temperature": 0.2,
                },
                timeout=90,
            )

            if res.status_code != 200:
                print("AI HTTP ERROR:", res.status_code, res.text[:200])
                return {}

            content = res.json()["choices"][0]["message"]["content"]
            content = re.sub(r"```json|```", "", content).strip()
            match   = re.search(r"\{.*\}", content, re.S)
            if not match:
                print("AI PARSE ERROR — raw:", content[:300])
                return {}

            data = json.loads(match.group(0))
            return data if isinstance(data, dict) else {}

        except Exception as e:
            print("AI call error:", e)
            return {}

    # =========================================================
    # RANK
    # =========================================================

    def rank(self, jobs):
        return sorted(
            jobs,
            key=lambda j: j.get("ai_score", 0) + max(0, 3 - j.get("days_old", 7)),
            reverse=True,
        )

    # =========================================================
    # SELECT
    # =========================================================

    def select(self, jobs):
        apply_list  = [j for j in jobs if j.get("ai_score", 0) >= self.min_apply_score]
        review_list = [j for j in jobs if 10 <= j.get("ai_score", 0) < self.min_apply_score]

        if review_list:
            print(f"\n── REVIEW MANUALLY ({len(review_list)}) ──")
            for j in review_list:
                print(
                    f"  score={j.get('ai_score')}  {j.get('title')} @ {j.get('company')}"
                    f"  |  {j.get('ai_reason', '')}"
                )

        return apply_list[:self.daily_apply_limit]

    # =========================================================
    # CACHE
    # =========================================================

    def _load_cache(self):
        if os.path.exists(self.cache_file):
            try:
                with open(self.cache_file) as f:
                    return json.load(f)
            except Exception:
                return {}
        return {}

    def _save_cache(self):
        with open(self.cache_file, "w") as f:
            json.dump(self.cache, f, indent=2)
