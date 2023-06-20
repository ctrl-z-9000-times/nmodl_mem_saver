import argparse
import json
import math
import matplotlib.pyplot as plt
import numpy as np

parser = argparse.ArgumentParser()

parser.add_argument('json_report1', type=str)
parser.add_argument('json_report2', type=str)

args = parser.parse_args()

with open(args.json_report1, 'rt') as f: json_report1 = json.load(f)
with open(args.json_report2, 'rt') as f: json_report2 = json.load(f)

m1 = set(int(x) for x in json_report1.keys())
m2 = set(int(x) for x in json_report2.keys())
models = sorted((m1 & m2) - {0})

print('Num Models:', len(models))

t1 = [json_report1[str(x)]['run_times'].get('model', math.nan) for x in models]
t2 = [json_report2[str(x)]['run_times'].get('model', math.nan) for x in models]
# Discard missing data points.
for idx in reversed(range(len(models))):
    if math.isnan(t1[idx]) or math.isnan(t2[idx]):
        t1.pop(idx)
        t2.pop(idx)

# Discard models that run too quickly to measure.
threshold = 1
for idx, (a,b) in reversed(list(enumerate(zip(t1, t2)))):
    if a < threshold or b < threshold:
        t1.pop(idx)
        t2.pop(idx)

print('Num Measurements:', len(t1))
print("Min Run Time:", threshold, 'seconds')

pct = [100 * (a / b - 1) for a,b in zip(t1, t2)]

print("Speedup:")
print('Min', round(np.min(pct), 2), '%')
print('Max', round(np.max(pct), 2), '%')
print('Avg', round(np.mean(pct), 2), '%')
print('Std', round(np.std(pct), 2), '%')

plt.title("Percent Speedup")
plt.hist(pct, bins=40)
plt.show()

