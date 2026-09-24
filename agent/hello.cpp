// ============================================================================
// hello.cpp - 最初の C++ プログラム
//
// C++ は Python と違い「コンパイル」が必要です。
//   コンパイル: g++ hello.cpp -o hello.exe
//   実行:       ./hello.exe
// ============================================================================

// #include は Python の import に相当します。
// <iostream> は「標準入出力ライブラリ」で、
// 画面への出力（print に相当）に必要です。
#include <iostream>

// std::string を使うために必要です。
// Python では str が組み込みですが、C++ ではライブラリを include します。
#include <string>

// ----------------------------------------------------------------------------
// main() 関数
//
// Python では「if __name__ == "__main__":」の中に書いていた処理を、
// C++ では main() 関数の中に書きます。
//
// int は「整数を返す」という意味です。
// プログラムが正常終了したら 0 を返すのがお約束です（exit code と同じ）。
// ----------------------------------------------------------------------------
int main() {
    // std::cout は Python の print() に相当します。
    //   Python: print("Hello")
    //   C++:    std::cout << "Hello" << std::endl;
    //
    // << は「流し込む」演算子で、文字列や変数を cout に流し込みます。
    // std::endl は改行（Python の print() が自動で付ける \n と同じ）。
    std::cout << "Hello, DeviceHub!" << std::endl;

    // C++ は型を明示して変数を宣言します。
    //   Python: name = "seiya"          （型を書かない）
    //   C++:    std::string name = "seiya";  （std::string と型を指定）
    std::string name = "seiya";

    // int は整数型です。
    //   Python: age = 25     （int と書かない）
    //   C++:    int age = 25;
    int year = 2026;

    // 変数も << で繋げて出力できます。
    std::cout << "Name: " << name << std::endl;
    std::cout << "Year: " << year << std::endl;

    // main() が 0 を返す = 正常終了（exit code 0 と同じ意味）
    return 0;
}
