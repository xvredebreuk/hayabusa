import copy
from collections import OrderedDict
from io import StringIO

import yaml
from sigma.backends.base import SingleTextQueryBackend
from sigma.parser.condition import SigmaAggregationParser
from sigma.parser.modifiers.base import SigmaTypeModifier
from sigma.parser.modifiers.type import SigmaRegularExpressionModifier

class HayabusaBackend(SingleTextQueryBackend):
    """Base class for backends that generate one text-based expression from a Sigma rule"""
    ## see tools.py
    ## use this value when sigmac parse argument of "-t"
    identifier = "hayabusa"
    active = True

    # the following class variables define the generation and behavior of queries from a parse tree some are prefilled with default values that are quite usual
    andToken = " and "                  # Token used for linking expressions with logical AND
    orToken = " or "                    # Same for OR
    notToken = " not "                  # Same for NOT
    subExpression = "(%s)"              # Syntax for subexpressions, usually parenthesis around it. %s is inner expression
    valueExpression = "%s"              # Expression of values, %s represents value
    typedValueExpression = dict()       # Expression of typed values generated by type modifiers. modifier identifier -> expression dict, %s represents value

    sort_condition_lists = False
    mapListsSpecialHandling = True

    name_idx = 1
    selection_prefix = "SELECTION_{0}"
    name_2_selection = OrderedDict()

    def __init__(self, sigmaconfig, options):
        super().__init__(sigmaconfig)

    def cleanValue(self, val):
        return val

    def generateListNode(self, node):
        return self.generateORNode(node)

    def create_new_selection(self):
        name = self.selection_prefix.format(self.name_idx)
        self.name_idx+=1
        return name

    def generateMapItemNode(self, node):
        fieldname, value = node

        transformed_fieldname = self.fieldNameMapping(fieldname, value)
        if self.mapListsSpecialHandling == False and type(value) in (str, int, list) or self.mapListsSpecialHandling == True and type(value) in (str, int):
            name = self.create_new_selection()
            self.name_2_selection[name] = [(transformed_fieldname, self.generateNode(value))]
            return name
        elif type(value) == list:
            return self.generateMapItemListNode(transformed_fieldname, value)
        elif isinstance(value, SigmaTypeModifier):
            return self.generateMapItemTypedNode(transformed_fieldname, value)
        elif value is None:
            return self.generateNode((transformed_fieldname+"|re","^$")) #nullは正規表現で表す。これでいいのかちょっと不安
        else:
            raise TypeError("Backend does not support map values of type " + str(type(value)))

    def generateMapItemTypedNode(self, fieldname, value):
        # `|re`オプションに対応
        if type(value) == SigmaRegularExpressionModifier:
            fieldname = fieldname + "|re"
            return self.generateNode((fieldname,value.value))
        else:
            raise NotImplementedError("Type modifier '{}' is not supported by backend".format(value.identifier))

    def generateMapItemListNode(self, fieldname, value):
        ### 下記のようなケースに対応
        ### selection:
        ###     EventID:
        ###         - 1
        ###         - 2

        ### 基本的にリストはORと良く、generateListNodeもORNodeを生成している。
        ### しかし、上記のケースでgenerateListNode()を実行すると、下記のようなYAMLになってしまう。
        ### selection:
        ###     EventID: 1 or 2

        ### 上記のようにならないように、修正している。
        ### なお、generateMapItemListNode()を有効にするために、self.mapListsSpecialHandling = Trueとしている
        list_values = list()
        for sub_node in value:
            list_values.append((fieldname,sub_node))

        return self.subExpression % self.generateORNode(list_values) 

    def generateAggregation(self, agg):
        # python3 tools/sigmac rules/windows/process_creation/win_dnscat2_powershell_implementation.yml --config tools/config/generic/sysmon.yml --target hayabusa
        if agg == None:
            return ""
        if agg.aggfunc == SigmaAggregationParser.AGGFUNC_COUNT:
            # condition の中に "|" は1つのみ
            # | 以降をそのまま出力する
            target = '|'
            index = agg.parser.parsedyaml["detection"]["condition"].find(target)
            return agg.parser.parsedyaml["detection"]["condition"][index:]

        ## count以外は対応していないので、エラーを返す
        raise NotImplementedError("This rule contains aggregation operator not implemented for this backend")

    def generateValueNode(self, node):
        ## このメソッドをオーバーライドしておかないとint型もstr型として扱われてしまうので、int型やint型として、str型はstr型として処理するために実装した。
        ## このメソッドは最悪無くてもいいような気もする。

        if type(node) == int:
            return node
        else:
            return self.valueExpression % (self.cleanValue(str(node)))

    def generateQuery(self, parsed):
        result = self.generateNode(parsed.parsedSearch)
        if parsed.parsedAgg:
            res = self.generateAggregation(parsed.parsedAgg)
            result += res
        ret = ""
        with StringIO() as bs:
            ## 元のyamlをいじるとこの後の処理に影響を与える可能性があるので、deepCopyする
            parsed_yaml = copy.deepcopy(parsed.sigmaParser.parsedyaml)
            ## なんかタイトルは先頭に来てほしいので、そのための処理
            ## parsed.sigmaParser.parsedyamlがOrderedDictならこんなことしなくていい、後で別のやり方があるか調べる
            ## 順番固定してもいいかも
            bs.write("title: " + parsed_yaml["title"]+"\n")
            del parsed_yaml["title"]

            ## detectionの部分だけ変更して出力する。
            parsed_yaml["detection"] = {}
            parsed_yaml["detection"]["condition"] = result
            for key, values in self.name_2_selection.items():
                parsed_yaml["detection"][key] = {}
                for fieldname, value in values:
                    parsed_yaml["detection"][key][fieldname] = value

            yaml.dump(parsed_yaml, bs, indent=4, default_flow_style=False)
            ret = bs.getvalue()

        return ret
