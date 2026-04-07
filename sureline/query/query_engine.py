"""
Sureline — Query Engine (Tool-Calling Architecture)

Uses Ollama LLM via TOOL CALLS (not raw generation) to:
1. Understand the user's natural language question
2. Call the appropriate tool (run_sql_query or run_pandas_query)
3. Return structured results

Tool-calling is faster than raw generation because the model only
needs to output a small JSON object (tool name + arguments) rather
than generating free-form SQL/code text.
"""

import json
import logging
import time
from pathlib import Path
from typing import Any, Optional

import ollama

from sureline.config import DB_PATH, DATA_DIR, OLLAMA_BASE_URL
from sureline.query.schema_loader import get_full_schema
from sureline.query.sandbox import execute_sql, execute_pandas, QueryResult

logger = logging.getLogger(__name__)

# ─── Tool Definitions (OpenAI function-calling format) ──────────
QUERY_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "run_sql_query",
            "description": (
                "Execute a read-only SQL query against the Mahakash company SQLite database. "
                "Use this for questions about employees, sales, products, clients, or expenses. "
                "The database uses Indian Rupees (INR) for all amounts. "
                "Only SELECT statements are allowed."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "sql": {
                        "type": "string",
                        "description": "A valid SQLite SELECT query to answer the user's question.",
                    }
                },
                "required": ["sql"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_pandas_query",
            "description": (
                "Execute Python pandas code to analyze data from the Mahakash sales CSV file. "
                "Use this when complex aggregation, pivoting, or multi-step data analysis is needed. "
                "The DataFrame is pre-loaded as `df`. Your code must assign the final output to `result`."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "code": {
                        "type": "string",
                        "description": (
                            "Python pandas code. The DataFrame `df` is pre-loaded from the sales CSV. "
                            "You must assign your answer to a variable named `result`."
                        ),
                    }
                },
                "required": ["code"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "no_data_query_needed",
            "description": (
                "Use this when the user's question does NOT require querying the database "
                "or when you can answer from general knowledge about Mahakash. "
                "For example: 'What does Mahakash do?' or 'Tell me about your products.'"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "reason": {
                        "type": "string",
                        "description": "Why no database query is needed.",
                    }
                },
                "required": ["reason"],
            },
        },
    },
]


class QueryEngine:
    """
    Translates natural language questions into data queries via tool calling.

    Uses Ollama with function/tool calling to generate SQL or Pandas queries,
    then executes them in a sandbox and returns results.
    """

    def __init__(self, model_name: str = "qwen2.5:3b", db_path: Optional[Path] = None):
        self.model_name = model_name
        self.db_path = db_path or DB_PATH
        self.csv_path = DATA_DIR / "mahakash_sales.csv"

        # Load schema once at init
        self._schema = get_full_schema(self.db_path, self.csv_path)

        self._system_prompt = self._build_system_prompt()

        logger.info(f"QueryEngine initialized with model={model_name}")

    def _build_system_prompt(self) -> str:
        return f"""You are a data query assistant for Mahakash Space Private Limited, an Indian space company.

Your ONLY job is to answer user questions by calling the appropriate tool.

RULES:
1. For questions about data (employees, sales, products, clients, expenses), call run_sql_query or run_pandas_query.
2. For general questions about the company, call no_data_query_needed.
3. Always use the EXACT table and column names from the schema below.
4. All monetary amounts are in Indian Rupees (INR).
5. Only generate SELECT queries — no writes.
6. Keep queries simple and efficient.

DATABASE SCHEMA:
{self._schema}
"""

    def query(self, question: str) -> QueryResult:
        """
        Process a natural language question using tool calling.

        Args:
            question: The user's question in natural language.

        Returns:
            QueryResult with the query results or error.
        """
        start_time = time.time()

        try:
            # Call Ollama with tool definitions
            response = ollama.chat(
                model=self.model_name,
                messages=[
                    {"role": "system", "content": self._system_prompt},
                    {"role": "user", "content": question},
                ],
                tools=QUERY_TOOLS,
                options={
                    "temperature": 0.1,  # Low temp for deterministic tool calls
                    "num_predict": 512,  # Limit output length for speed
                },
            )

            # Check if the model made a tool call
            message = response.get("message", {})
            tool_calls = message.get("tool_calls", [])

            if not tool_calls:
                # Model didn't call a tool — return the text response as-is
                elapsed = (time.time() - start_time) * 1000
                return QueryResult(
                    success=True,
                    data=message.get("content", "I'm not sure how to answer that."),
                    query_type="text",
                    execution_time_ms=elapsed,
                )

            # Process the first tool call
            tool_call = tool_calls[0]
            func_name = tool_call["function"]["name"]
            func_args = tool_call["function"]["arguments"]

            logger.info(f"Tool call: {func_name}({json.dumps(func_args)[:200]})")

            if func_name == "run_sql_query":
                result = execute_sql(self.db_path, func_args["sql"])

            elif func_name == "run_pandas_query":
                result = execute_pandas(self.csv_path, func_args["code"])

            elif func_name == "no_data_query_needed":
                elapsed = (time.time() - start_time) * 1000
                return QueryResult(
                    success=True,
                    data=func_args.get("reason", "This is a general knowledge question."),
                    query_type="none",
                    execution_time_ms=elapsed,
                )

            else:
                elapsed = (time.time() - start_time) * 1000
                return QueryResult(
                    success=False,
                    error=f"Unknown tool called: {func_name}",
                    execution_time_ms=elapsed,
                )

            # Update timing to include LLM inference time
            result.execution_time_ms = (time.time() - start_time) * 1000
            return result

        except Exception as e:
            elapsed = (time.time() - start_time) * 1000
            logger.error(f"Query engine error: {e}", exc_info=True)
            return QueryResult(
                success=False,
                error=str(e),
                execution_time_ms=elapsed,
            )


if __name__ == "__main__":
    # Quick test
    engine = QueryEngine()

    test_questions = [
        "What were Mahakash's total sales last quarter?",
        "Who is the highest paid employee?",
        "How many products do we have?",
    ]

    for q in test_questions:
        print(f"\n❓ {q}")
        result = engine.query(q)
        if result.success:
            print(f"✅ Query: {result.generated_query}")
            print(f"📊 Result: {result.data}")
        else:
            print(f"❌ Error: {result.error}")
        print(f"⏱️  Time: {result.execution_time_ms:.0f}ms")
