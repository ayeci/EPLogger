import json
data_str = open('temp_data7.json', encoding='utf-8').read().replace('callback(','').replace(');','')
val = json.loads(data_str)
labels = val['labels_24h']
hourly = val['hourly_weather_summary']
print("Testing labels:")
for i, timeLabel in enumerate(labels):
    if timeLabel.endswith(':00') and hourly:
        matchingKeys = [k for k in hourly.keys() if k.endswith(' ' + timeLabel)]
        if matchingKeys:
            icon = hourly[matchingKeys[-1]]
            print(f"Index {i}, timeLabel {timeLabel}, matched: {matchingKeys[-1]}, icon {icon}")
        else:
            print(f"Index {i}, timeLabel {timeLabel}, NO MATCH")
