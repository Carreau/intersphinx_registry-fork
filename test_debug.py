import re

line = "`setuptools documentation <https://setuptools.pypa.io/en/latest/setuptools.html>`__"
url = "https://setuptools.pypa.io/en/latest/setuptools.html"
pattern = r"`([^`<>]+)\s*<" + re.escape(url) + r">`__?"
match = re.search(pattern, line)
print(f"Pattern: {pattern}")
print(f"Line: {line}")
print(f"Match: {match}")
if match:
    print(f"Group 0: {match.group(0)}")
    print(f"Group 1: {match.group(1)}")
    print(f"Start: {match.start()}, End: {match.end()}")
else:
    print("No match!")
