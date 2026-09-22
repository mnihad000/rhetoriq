/*
 * Licensed to the Apache Software Foundation (ASF) under one or more
 * contributor license agreements. See the NOTICE file distributed with
 * this work for additional information regarding copyright ownership.
 * The ASF licenses this file to you under the Apache License, Version 2.0
 * (the "License"); you may not use this file except in compliance with
 * the License. You may obtain a copy of the License at
 *
 *     http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */

package org.apache.flink.streaming.connectors.kafka.table;

import org.apache.flink.table.types.DataType;
import org.apache.flink.table.types.FieldsDataType;
import org.apache.flink.table.types.logical.LogicalType;
import org.apache.flink.table.types.logical.LogicalTypeRoot;
import org.apache.flink.table.types.logical.RowType;
import org.apache.flink.table.types.logical.utils.LogicalTypeUtils;
import org.apache.flink.util.Preconditions;

import java.util.List;
import java.util.stream.Collectors;

/** Flink 2.3 compatibility helpers needed by the Kafka 4.0.1 connector source. */
final class KafkaDataTypeUtils {

    private KafkaDataTypeUtils() {}

    /**
     * Removes a string prefix from row field names while retaining conversion and child data types.
     *
     * <p>This is the implementation removed from Flink's public {@code DataTypeUtils} after 2.0.
     * Keeping it connector-local avoids adding a class to Flink's own package.
     */
    static DataType stripRowPrefix(DataType dataType, String prefix) {
        Preconditions.checkArgument(
                dataType.getLogicalType().is(LogicalTypeRoot.ROW), "Row data type expected.");
        final RowType rowType = (RowType) dataType.getLogicalType();
        final List<String> newFieldNames =
                rowType.getFieldNames().stream()
                        .map(name -> name.startsWith(prefix) ? name.substring(prefix.length()) : name)
                        .collect(Collectors.toList());
        final LogicalType newRowType = LogicalTypeUtils.renameRowFields(rowType, newFieldNames);
        return new FieldsDataType(
                newRowType, dataType.getConversionClass(), dataType.getChildren());
    }
}
