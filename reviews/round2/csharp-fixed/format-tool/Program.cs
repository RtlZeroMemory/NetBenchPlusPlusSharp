using Microsoft.CodeAnalysis;
using Microsoft.CodeAnalysis.CSharp;
using Microsoft.CodeAnalysis.CSharp.Syntax;

foreach (string path in Directory.GetFiles(args[0], "*.cs"))
{
    var root = CSharpSyntaxTree.ParseText(File.ReadAllText(path)).GetRoot();
    root = new ExpandedInitializers().Visit(root)!;
    File.WriteAllText(path, root.ToFullString());
}

sealed class ExpandedInitializers : CSharpSyntaxRewriter
{
    static SyntaxToken NewLine(SyntaxToken token) => token.WithTrailingTrivia(SyntaxFactory.EndOfLine("\n"));
    static SeparatedSyntaxList<T> Lines<T>(SeparatedSyntaxList<T> items) where T : SyntaxNode =>
        SyntaxFactory.SeparatedList(items, items.GetSeparators().Select(NewLine));

    public override SyntaxNode? VisitAnonymousObjectCreationExpression(AnonymousObjectCreationExpressionSyntax node)
    {
        node = (AnonymousObjectCreationExpressionSyntax)base.VisitAnonymousObjectCreationExpression(node)!;
        return node.WithOpenBraceToken(NewLine(node.OpenBraceToken))
            .WithInitializers(Lines(node.Initializers))
            .WithCloseBraceToken(node.CloseBraceToken.WithLeadingTrivia(SyntaxFactory.EndOfLine("\n")));
    }

    public override SyntaxNode? VisitCollectionExpression(CollectionExpressionSyntax node)
    {
        node = (CollectionExpressionSyntax)base.VisitCollectionExpression(node)!;
        if (node.Elements.Count < 6) return node;
        return node.WithOpenBracketToken(NewLine(node.OpenBracketToken))
            .WithElements(Lines(node.Elements))
            .WithCloseBracketToken(node.CloseBracketToken.WithLeadingTrivia(SyntaxFactory.EndOfLine("\n")));
    }

    public override SyntaxNode? VisitClassDeclaration(ClassDeclarationSyntax node)
    {
        node = (ClassDeclarationSyntax)base.VisitClassDeclaration(node)!;
        var members = new List<MemberDeclarationSyntax>();
        foreach (var member in node.Members)
        {
            if (member is FieldDeclarationSyntax field && field.Declaration.Variables.Count > 3)
            {
                foreach (var variable in field.Declaration.Variables)
                {
                    members.Add(field.WithDeclaration(field.Declaration.WithVariables(SyntaxFactory.SingletonSeparatedList(variable)))
                        .WithSemicolonToken(NewLine(field.SemicolonToken)));
                }
            }
            else members.Add(member);
        }
        return node.WithMembers(SyntaxFactory.List(members));
    }

    public override SyntaxNode? VisitArgumentList(ArgumentListSyntax node)
    {
        node = (ArgumentListSyntax)base.VisitArgumentList(node)!;
        return node.Arguments.Count > 4 && node.ToFullString().Length > 150
            ? node.WithOpenParenToken(NewLine(node.OpenParenToken)).WithArguments(Lines(node.Arguments)) : node;
    }

    public override SyntaxNode? VisitObjectCreationExpression(ObjectCreationExpressionSyntax node)
    {
        node = (ObjectCreationExpressionSyntax)base.VisitObjectCreationExpression(node)!;
        if (node.Initializer is not { } initializer || initializer.Expressions.Count < 2) return node;
        return node.WithInitializer(initializer.WithOpenBraceToken(NewLine(initializer.OpenBraceToken))
            .WithExpressions(Lines(initializer.Expressions))
            .WithCloseBraceToken(initializer.CloseBraceToken.WithLeadingTrivia(SyntaxFactory.EndOfLine("\n"))));
    }
}
