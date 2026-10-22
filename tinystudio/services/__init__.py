"""Application services: every read and write of the operational database goes through here.

Functions take a SQLAlchemy ``Session`` (the caller owns the transaction) and the ``Project`` row they act
on. The API, the worker, and the CLI all call the same functions; nothing else touches the tables.
"""
