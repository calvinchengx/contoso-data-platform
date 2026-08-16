{% macro flag(value) -%}
  {%- if target.type in ['fabric', 'sqlserver', 'synapse'] -%}
    cast({{ value }} as bit)
  {%- else -%}
    cast({{ value }} as boolean)
  {%- endif -%}
{%- endmacro %}

{% macro date_quarter(col) -%}
  {%- if target.type in ['fabric', 'sqlserver', 'synapse'] -%}
    datepart(quarter, {{ col }})
  {%- else -%}
    quarter({{ col }})
  {%- endif -%}
{%- endmacro %}

{% macro varchar_n(n) -%}
  {%- if target.type == 'databricks' -%}
    string
  {%- else -%}
    varchar({{ n }})
  {%- endif -%}
{%- endmacro %}

{% macro str_concat(a, b) -%}
  {%- if target.type in ['fabric', 'sqlserver', 'synapse'] -%}
    {{ a }} + {{ b }}
  {%- else -%}
    concat({{ a }}, {{ b }})
  {%- endif -%}
{%- endmacro %}
