"""Code mode's own domain package: workspace path containment and the
pending-changes (changeset) layer that file edits land in before disk.

Split out of tools/coding.py because both the tools AND the HTTP routes need
these pieces. A tool importing from a route module (or vice versa) would be a
dependency cycle; a shared domain package is the normal way out.
"""
