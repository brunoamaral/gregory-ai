# Streaming CSV Response Implementation

## Overview

This document explains the implementation of streaming CSV responses in the Gregory API. This feature allows the API to stream large datasets as CSV files without loading all data into memory at once, improving performance and reducing the likelihood of timeouts.

## Key Components

We have implemented two approaches for streaming CSV responses:

### 1. Middleware-Based Approach

1. **StreamingCSVRenderer**: A custom renderer that extends `FlattenedCSVRenderer` to support streaming responses.
2. **StreamingCSVMiddleware**: A middleware that converts regular responses to streaming responses when needed.
3. **Django's StreamingHttpResponse**: Used to stream the response to the client.

### 2. Direct Streaming Approach

1. **DirectStreamingCSVRenderer**: A simpler renderer that directly returns a `StreamingHttpResponse`.
2. **Django's StreamingHttpResponse**: Used to stream the response to the client.

## How It Works

### Middleware-Based Approach

1. The `StreamingCSVRenderer` generates CSV data row by row using a generator function
2. The renderer stores this generator in `self.generator_function` and sets `streaming=True` on the response
3. The middleware detects the streaming flag and creates a `StreamingHttpResponse` using the stored generator
4. The middleware copies headers from the original response to the streaming response

### Direct Streaming Approach

1. The `DirectStreamingCSVRenderer` prepares data and creates a generator function for CSV rows
2. The renderer returns a `StreamingHttpResponse` directly, bypassing the normal response flow
3. The renderer sets appropriate headers on the streaming response

## Current Implementation

The current implementation uses the **Direct Streaming Approach** (`DirectStreamingCSVRenderer`) as the default for CSV responses. This approach was chosen for its simplicity and reliability.

### For API Users

Any endpoint that accepts the `format=csv` parameter will now automatically stream the response. For example:

- `/articles/?format=csv` - Stream all articles as CSV
- `/trials/?format=csv` - Stream all clinical trials as CSV
- `/teams/1/articles/?format=csv` - Stream team articles as CSV

These endpoints support all the usual filtering parameters.

### For Developers

No special setup is required - all CSV responses are automatically streamed. The StreamingCSVRenderer is registered as the default renderer for CSV responses in settings.py.

## Benefits

- **Reduced Memory Usage**: Only processes one row at a time, reducing server memory requirements
- **Faster Initial Response**: Starts sending data immediately without waiting for all data to be processed
- **Better for Large Datasets**: Can handle much larger datasets without timing out
- **Same Filtering Capabilities**: Supports all the same filters as regular endpoints
- **Seamless Integration**: Works automatically with all existing endpoints
- **Clean Text Fields**: Line breaks in text fields (like summaries) are automatically removed for better CSV compatibility

## Implementation Details

### CSV Generation

2. **Cleans Text Fields**: Line breaks in text fields like summaries are removed and replaced with spaces
3. **Row-by-Row Processing**: Data is processed one row at a time to minimize memory usage

### Response Headers

The streaming response includes the following headers:

- `Content-Type: text/csv; charset=utf-8`
- `Content-Disposition: attachment; filename="gregory-ai-{object_type}-{current_date}.csv"`

## Troubleshooting

### Binary Data in Response

If binary data appears in the response instead of CSV content, it may indicate that:

1. The middleware is improperly handling the generator function
2. The content is being prematurely consumed
3. The wrong renderer is being applied

**Root Cause of Binary Data Issue:**

The original `StreamingCSVMiddleware` had a critical bug where it would use `response.content` directly for the streaming response, but this was already the binary representation of the generator object, not the actual CSV rows. This caused binary data to be sent to clients instead of CSV.

This was fixed by:
1. Properly storing the generator function in the renderer
2. Having the middleware access the generator through `renderer.generator_function` 
3. Creating the `DirectStreamingCSVRenderer` as a more robust alternative

### Memory Issues

If memory usage is still high despite streaming:

1. Check for any code that might be collecting all rows before sending
2. Ensure pagination is working correctly
3. Verify that the generator function is properly yielding one row at a time

## Conclusion

The streaming CSV implementation significantly improves performance and reliability when exporting large datasets. The direct streaming approach provides the most robust solution and is the recommended implementation going forward.