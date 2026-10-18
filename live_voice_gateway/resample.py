"""PCM16 mono resampling for the device and gpt-live-1 rates."""

import array


def resample_pcm16(pcm: bytes, source_rate: int, target_rate: int) -> bytes:
    """Resample little-endian PCM16 with linear interpolation.

    Equal rates return the even-length input unchanged. The Live API uses
    24 kHz in both directions; Voice PE capture is 16 kHz and the ReSpeaker
    XVF3800 capture path is 48 kHz.
    """
    if source_rate <= 0 or target_rate <= 0:
        raise ValueError("sample rates must be positive")
    even = pcm[: len(pcm) - (len(pcm) % 2)]
    if source_rate == target_rate or not even:
        return even

    samples = array.array("h")
    samples.frombytes(even)
    count = len(samples)
    output_count = int(count * target_rate / source_rate)
    if output_count <= 0:
        return b""

    output = array.array("h")
    last = count - 1
    for index in range(output_count):
        position = index * source_rate / target_rate
        left = int(position)
        if left >= last:
            output.append(samples[last])
            continue
        fraction = position - left
        start = samples[left]
        end = samples[left + 1]
        output.append(int(start + (end - start) * fraction))
    return output.tobytes()
