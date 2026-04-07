"""
Sureline — Query Engine (Tool-Calling Architecture)

Uses the active LLM provider via TOOL CALLS (not raw generation) to:
1. Understand the user's natural language question
2. Call the appropriate tool (run_sql_query or run_pandas_query)
3. Return structured results

Tool-calling is faster than raw generation because the model only
needs to output a small JSON object (tool name + arguments) rather
than generating free-form SQL/code text.
"""

import asyncio
import json
import logging
import time
from pathlib import Path
from typing import Any, Optional

import pandas as pd

from sureline.config import DB_PATH, DATA_DIR, create_llm_client
from sureline.query.schema_loader import get_full_schema
from sureline.query.sandbox import execute_sql, execute_pandas, QueryResult

logger = logging.getLogger(__name__)


def _build_tools(client_name: str, company_description: str) -> list[dict]:
    """Build OpenAI tool definitions parameterised for the active client."""
    return [
        {
            "type": "function",
            "function": {
                "name": "run_sql_query",
                "description": (
                    f"Execute a read-only SQL query against the {client_name} SQLite database. "
                    f"{company_description} "
                    "Use this for questions about structured data. "
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
                    f"Execute Python pandas code to analyze data from the {client_name} CSV file. "
                    "Use this when complex aggregation, pivoting, or multi-step analysis is needed. "
                    "The DataFrame is pre-loaded as `df`. Your code must assign the final output to `result`."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "code": {
                            "type": "string",
                            "description": (
                                "Python pandas code. The DataFrame `df` is pre-loaded. "
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
                    f"Use this when the user's question does NOT require querying the database "
                    f"or when you can answer from general knowledge about {client_name}. "
                    "For example: 'What does the company do?' or 'Tell me about your services.'"
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

    Uses the active LLM provider (Azure → OpenAI → Gemini → Ollama) with
    function/tool calling to generate SQL or Pandas queries, executes them
    in a sandbox, and returns results.
    """

    def __init__(
        self,
        db_path: Optional[Path] = None,
        client_name: str = "the company",
        company_description: str = "",
    ):
        self.db_path = db_path or DB_PATH
        self.csv_path = DATA_DIR / "sales.csv"
        self._client, self._model = create_llm_client()

        self._tools = _build_tools(client_name, company_description)

        # Cache CSV DataFrame at init — avoids repeated disk I/O on every pandas query
        self._csv_df = pd.read_csv(self.csv_path) if self.csv_path.exists() else None

        # Load schema once at init
        self._schema = get_full_schema(self.db_path, self.csv_path)

        self._system_prompt = (
            f"You are a data query assistant for {client_name}.\n\n"
            "Your ONLY job is to answer user questions by calling the appropriate tool.\n\n"
            "RULES:\n"
            "1. For questions about data, call run_sql_query or run_pandas_query.\n"
            "2. For general questions about the company, call no_data_query_needed.\n"
            "3. Always use the EXACT table and column names from the schema below.\n"
            "4. Only generate SELECT queries — no writes.\n"
            "5. Keep queries simple and efficient.\n\n"
            f"DATABASE SCHEMA:\n{self._schema}\n"
        )

        logger.info("QueryEngine initialized (model=%s, client=%s)", self._model, client_name)

    async def query(self, question: str) -> QueryResult:
        """
        Process a natural language question using tool calling.

        Args:
            question: The user's question in natural language.

        Returns:
            QueryResult with the query results or error.
        """
        start_time = time.time()

        try:
            response = await self._client.chat.completions.create(
                model=self._model,
                messages=[
                    {"role": "system", "content": self._system_prompt},
                    {"role": "user", "content": question},
                ],
                tools=self._tools,
                tool_choice="auto",
                temperature=0.1,
                max_tokens=512,
            )

            message = response.choices[0].message
            tool_calls = message.tool_calls or []

            if not tool_calls:
                elapsed = (time.time() - start_time) * 1000
                return QueryResult(
                    success=True,
                    data=message.content or "I'm not sure how to answer that.",
                    query_type="text",
                    execution_time_ms=elapsed,
                )

            tool_call = tool_calls[0]
            func_name = tool_call.function.name
            func_args = json.loads(tool_call.function.arguments)

            logger.info("Tool call: %s(%s)", func_name, json.dumps(func_args)[:200])

            if func_name == "run_sql_query":
                result = await asyncio.to_thread(execute_sql, self.db_path, func_args["sql"])

            elif func_name == "run_pandas_query":
                result = await asyncio.to_thread(
                    execute_pandas, self.csv_path, func_args["code"], self._csv_df
                )

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

            result.execution_time_ms = (time.time() - start_time) * 1000
            return result

        except Exception as e:
            elapsed = (time.time() - start_time) * 1000
            logger.error("Query engine error: %s", e, exc_info=True)
            return QueryResult(
                success=False,
                error=str(e),
                execution_time_ms=elapsed,
            )


if __name__ == "__main__":
    async def _main():
        engine = QueryEngine()
        questions = [
            "What were total sales last quarter?",
            "Who is the highest paid employee?",
            "How many products do we have?",
        ]
        for q in questions:
            print(f"\n? {q}")
            result = await engine.query(q)
            if result.success:
                print(f"  Query: {result.generated_query}")
                print(f"  Result: {result.data}")
            else:
                print(f"  Error: {result.error}")
            print(f"  Time: {result.execution_time_ms:.0f}ms")

    asyncio.run(_main())
