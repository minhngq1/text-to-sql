from pydantic import BaseModel, ConfigDict, Field


# Vietnamese question submitted to generate SQL
class QueryRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    question: str = Field(..., min_length=5, max_length=500)


# SQL statement to explain
class ExplainRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    sql: str = Field(..., min_length=10, max_length=5000)


# SQL statement manually edited by the user before running
class ExecuteRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    sql: str = Field(..., min_length=10, max_length=5000)
